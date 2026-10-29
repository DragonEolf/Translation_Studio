"""Regressions for automatic transliteration and guarded shared layout."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as N
import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app.main import create_app
from app.config import Settings, StudioError
from app.models import database, Document, Page, Block, Translation
from app.services.cell_layout import CellLayout
from app.services.paragraph_flow import ParagraphFlow
from app.services.text_fitter import FontRegistry
from test_cell_layout import entry, assert_fits
from test_transliteration import Echo, Pronunciations, wait


def test_initial_transliteration_runs_before_pdf_and_preserves_literals(tmp_path):
    pronunciation = Pronunciations()
    app = create_app(Settings(_env_file=None, studio_data_dir=tmp_path,
        studio_font_dir=Path('fonts').resolve()), provider=Echo(), transliteration_provider=pronunciation)
    with fitz.open() as pdf:
        pdf.new_page(width=500, height=200).insert_text((30, 55), 'VGF API 250 INR https://example.com', fontsize=12)
        data = pdf.tobytes()
    with TestClient(app) as client:
        uploaded = client.post('/v1/documents', files={'file': ('input.pdf', data)})
        doc = uploaded.json()['document_id']
        assert wait(client, uploaded)['status'] == 'completed'
        result = wait(client, client.post(f'/v1/documents/{doc}/translations', json={
            'target_language': 'ta', 'layout_mode': 'paragraphs', 'transliterate': True}))
        assert result['status'] in {'completed', 'completed_with_issues'}, result
        assert sorted(pronunciation.calls) == sorted([('vee gee eff', 'ta'), ('ay pee eye', 'ta')])
        blocks = client.get(f'/v1/documents/{doc}/blocks').json()
        translated = ''.join(b['translation']['text'] for b in blocks)
        assert 'வீ ஜி எஃப்' in translated and 'ஏ பி ஐ' in translated
        assert '250 INR https://example.com' in translated
        assert all(b['translation']['metadata_json']['automatic_transliteration'] for b in blocks)
        report = client.get(f'/v1/documents/{doc}/artifacts/layout-report.json').json()
        assert report['pages'][0]['blocks'][0]['translation_retained']
        assert report['pages'][0]['blocks'][0]['translation_id'] == blocks[0]['translation']['id']
        with fitz.open(stream=client.get(f'/v1/documents/{doc}/artifacts/final.pdf').content, filetype='pdf') as pdf:
            assert 'வீ ஜி எஃப்' in pdf[0].get_text()
        rejected = client.post(f'/v1/documents/{doc}/translations', json={'target_language': 'en', 'transliterate': True})
        assert rejected.status_code == 422


def test_transliteration_failure_does_not_apply_partial_replacements(tmp_path):
    class Failure(Pronunciations):
        async def transliterate(self, text, language):
            if self.calls:
                raise StudioError('provider_unavailable', 'Offline', 503)
            return await super().transliterate(text, language)
    app = create_app(Settings(_env_file=None, studio_data_dir=tmp_path,
        studio_font_dir=Path('fonts').resolve()), Echo(), transliteration_provider=Failure())
    with fitz.open() as pdf:
        pdf.new_page().insert_text((30,55), 'VGF API')
        data = pdf.tobytes()
    with TestClient(app) as c:
        upload = c.post('/v1/documents', files={'file': ('test.pdf', data)})
        doc = upload.json()['document_id']; wait(c, upload)
        result = wait(c, c.post(f'/v1/documents/{doc}/translations', json={'target_language':'ta', 'transliterate':True}))
        assert result['status'] == 'failed'
        assert c.get(f'/v1/documents/{doc}/blocks').json()[0]['translation']['text'] == 'VGF API'


def test_cell_recovers_overflow_at_readable_size_and_leaves_impossible_cell_unchanged():
    fonts = FontRegistry(Path('fonts').resolve())
    es = [entry(0,12,25,10,'A longer paragraph with many words. '*4), entry(1,12,60,10,'Short paragraph')]
    es[0][2].fit_status = 'overflow'
    texts = deepcopy([t.metadata_json for _, t, _ in es])
    assert len(CellLayout(fonts).apply(es, [b for b,_,_ in es], N(width=360,height=240))) == 2
    assert all(l.fit_status == 'fitted' and l.font_size >= 6 for _,_,l in es)
    assert_fits(es, fonts)
    assert texts == [t.metadata_json for _,t,_ in es]
    es = [entry(0,12,25,10,'Impossible content '*2000), entry(1,12,60,10,'Short')]
    es[0][2].fit_status = 'overflow'
    before = deepcopy([vars(l) for _,_,l in es])
    assert CellLayout(fonts).apply(es,[b for b,_,_ in es],N(width=360,height=240)) == []
    assert before == [vars(l) for _,_,l in es]


def paragraphs():
    es = [entry(0,12,25,15,'Several words that need more space. '*2), entry(1,12,48,60,'Short text')]
    for i, (b,t,l) in enumerate(es):
        b.style.pop('table_cell_bbox')
        b.type='paragraph'; b.source_text=t.text.removeprefix('● '); b.reading_order=i
        b.bbox=[30,b.bbox[1],320,b.bbox[3]]
    es[0][2].fit_status='overflow'
    return es


def test_neighbor_reflow_preserves_paragraph_text_and_styles():
    es = paragraphs(); fonts=FontRegistry(Path('fonts').resolve())
    texts=deepcopy([vars(t) for _,t,_ in es])
    assert ParagraphFlow(fonts).apply(es,[b for b,_,_ in es],N(width=360,height=240)) == ['0','1']
    assert all(l.fit_status == 'fitted' and l.font_size >= 6 for _,_,l in es)
    assert_fits(es,fonts)
    assert texts == [vars(t) for _,t,_ in es]


@pytest.mark.parametrize('barrier', ['heading','caption','list_item','footnote','table_cell','image','rule'])
def test_neighbor_reflow_respects_semantic_and_visible_barriers(barrier):
    es=paragraphs(); fonts=FontRegistry(Path('fonts').resolve()); graphics=[]
    if barrier == 'image': graphics=[[100,42,140,47]]
    elif barrier == 'rule': graphics=[[30,45,320,45]]
    else: es[1][0].type=barrier
    before=deepcopy([vars(l) for _,_,l in es])
    assert ParagraphFlow(fonts).apply(es,[b for b,_,_ in es],N(width=360,height=240),graphics) == []
    assert before == [vars(l) for _,_,l in es]


def test_database_enforces_constraints_and_reopens(tmp_path):
    path=tmp_path/'test.db'; engine, sessions=database(path)
    with sessions() as s:
        s.add(Document(id='doc',filename='test.pdf',sha256='x')); s.flush()
        s.add(Page(id='p',document_id='doc',number=1,width=100,height=100));s.flush()
        s.add(Block(id='b',document_id='doc',page_number=1,type='paragraph',reading_order=0,bbox=[1,1,5,5],source_text='text',style={})); s.commit()
    invalid = [
        "UPDATE documents SET status='typo' WHERE id='doc'",
        "INSERT INTO issues (id,document_id,block_id,code,severity,stage,message,resolution) VALUES ('i','doc','missing','overflow','error','checker','x','manual_review_required')",
        "UPDATE blocks SET page_number=2 WHERE id='b'",
        "INSERT INTO pages (id,document_id,number,width,height,dirty,extraction_status,metadata_json) VALUES ('p2','doc',1,100,100,1,'completed','{}')",
    ]
    for statement in invalid:
        with pytest.raises(IntegrityError), engine.begin() as c: c.execute(text(statement))
    with sessions() as s:
        s.add(Translation(block_id='b',target_language='en',script='native',text='x',revision=1));s.commit()
        s.add(Translation(block_id='b',target_language='en',script='native',text='x',revision=1))
        with pytest.raises(IntegrityError): s.commit()
    engine.dispose()
    engine,_=database(path); engine.dispose()


def test_single_cell_geometry_upgrade_retains_existing_translation(tmp_path):
    from app.orchestrator import latest
    with fitz.open() as pdf:
        page=pdf.new_page(width=400,height=200)
        for y in (20,65,110): page.draw_line((20,y),(380,y))
        for x in (20,200,380): page.draw_line((x,20),(x,110))
        for x,y,word in ((30,48,'Alpha'),(210,48,'Beta'),(30,92,'Gamma'),(210,92,'Delta')):
            page.insert_text((x,y),word,fontsize=12)
        data=pdf.tobytes()
    app=create_app(Settings(_env_file=None,studio_data_dir=tmp_path),Echo())
    with TestClient(app) as c:
        uploaded=c.post('/v1/documents',files={'file':('cells.pdf',data)})
        doc=uploaded.json()['document_id']; wait(c,uploaded)
        assert wait(c,c.post(f'/v1/documents/{doc}/translations',json={'target_language':'en'}))['status']=='completed'
        with app.state.sessions() as s:
            page=s.get(Page,f'{doc}-p1');page.metadata_json={**page.metadata_json,'layout_mode':'auto'};s.commit()
        job=wait(c,c.post(f'/v1/documents/{doc}/render'))
        assert job['status']=='completed',job
        blocks=c.get(f'/v1/documents/{doc}/blocks').json()
        assert len(blocks)==4
        assert all(b['translation'] and b['translation']['text']==b['source_text'] for b in blocks)
        assert all(b['translation']['metadata_json']['rich_text']['runs'] for b in blocks)


def test_render_rolls_back_reflow_that_introduces_checker_errors(tmp_path,monkeypatch):
    class BadFlow:
        def __init__(self,*args): pass
        def apply(self,entries,*args):
            if entries:
                entries[0][2].bbox=[10,10,1000,1000]
                return [entries[0][0].id]
            return []
    monkeypatch.setattr('app.orchestrator.ParagraphFlow',BadFlow)
    app=create_app(Settings(_env_file=None,studio_data_dir=tmp_path),Echo())
    with fitz.open() as pdf:
        pdf.new_page(width=300,height=200).insert_text((30,55),'Hello world')
        data=pdf.tobytes()
    with TestClient(app) as c:
        uploaded=c.post('/v1/documents',files={'file':('input.pdf',data)})
        doc=uploaded.json()['document_id'];wait(c,uploaded)
        job=wait(c,c.post(f'/v1/documents/{doc}/translations',json={'target_language':'en'}))
        assert job['status']=='completed',job
        page=c.get(f'/v1/documents/{doc}/artifacts/layout-report.json').json()['pages'][0]
        assert page['paragraph_changes']
        assert page['change_accepted'] is False
        assert page['issues_before']==page['issues_after']==0
        assert page['blocks'][0]['translation_retained']
        assert page['blocks'][0]['bbox']==page['blocks'][0]['before']['bbox']
