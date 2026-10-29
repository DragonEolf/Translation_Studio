from types import SimpleNamespace as N

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.main import create_app
from app.models import Block, Page, LayoutResult
from app.services.ocr_overlaps import overlap_reviews
from app.services.ocr_notation import display_text
from test_studio import FakeProvider, wait


def block(identity, text, bbox, ocr=True):
    return N(id=identity,source_text=text,bbox=bbox,style={'ocr':ocr})


def test_overlap_requires_small_math_region_mostly_inside_larger_ocr_passage():
    parent = block('parent', 'Retraining and interaction between agents\n(RE)', [137,462,508,547])
    suspect = block('suspect', '$$(192)$$', [249,531,281,551])
    assert overlap_reviews([parent,suspect]) == {'suspect':'parent'}
    isolated = block('isolated', '$$(192)$$', [249,560,281,580])
    currency = block('currency', '$100', suspect.bbox)
    plain = block('plain', '(192)', suspect.bbox)
    digital = block('digital', '$$(192)$$', suspect.bbox, ocr=False)
    assert overlap_reviews([parent,isolated,currency,plain,digital]) == {}
    assert suspect.source_text == '$$(192)$$'  # Retained for review, never deleted.


def test_balanced_numeric_display_math_is_rendered_without_delimiters():
    b = block('number', '$$(192)$$', [0,0,30,20])
    assert display_text(b, b.source_text) == '(192)'
    assert display_text(b, '$100 and $200') == '$100 and $200'
    assert display_text(b, '$$(192$$') == '$$(192$$'


@pytest.mark.parametrize('cache_enabled', [False, True])
def test_live_pipeline_and_saved_block_rerender_quarantine_overlap(tmp_path, cache_enabled):
    class OCR:
        async def extract(self, png):
            return {'choices':[{'finish_reason':'stop'}], 'blocks':[
                {'bbox_xyxy':[20,20,280,110], 'text':'Hello research on complex tasks\nRetraining and agent interaction\n(RE)', 'conf':.9},
                {'bbox_xyxy':[60,100,80,112], 'text':'$$(192)$$', 'conf':.9},
            ]}
    with fitz.open() as raster:
        p = raster.new_page(width=300,height=200)
        p.insert_text((30,80),'Handwriting (RE)')
        image = p.get_pixmap().tobytes('png')
    with fitz.open() as pdf:
        p = pdf.new_page(width=300,height=200)
        p.insert_image(p.rect,stream=image)
        data = pdf.tobytes()
    app = create_app(Settings(studio_data_dir=tmp_path,studio_ocr_dpi=72,studio_cache_enabled=cache_enabled),FakeProvider(),ocr_provider=OCR())
    with TestClient(app) as client:
        upload = client.post('/v1/documents',files={'file':('scan.pdf',data)})
        doc = upload.json()['document_id']
        wait(client,upload)
        job = wait(client,client.post(f'/v1/documents/{doc}/translations',json={'target_language':'en'}))
        assert job['status'] == 'completed_with_issues'
        issues = client.get(f'/v1/documents/{doc}/issues').json()
        assert any(i['code']=='ocr_overlap_review_required' for i in issues)
        with fitz.open(stream=client.get(f'/v1/documents/{doc}/artifacts/final.pdf').content,filetype='pdf') as pdf:
            assert '192' not in pdf[0].get_text()
            assert '(RE)' in pdf[0].get_text()
        # Existing stored blocks receive the same protection without re-OCR.
        with app.state.sessions() as session:
            child = session.get(Block, f'{doc}-p1-ocr1')
            child.style = {k:v for k,v in child.style.items() if k!='ocr_overlap_with'}
            page = session.scalar(select(Page).where(Page.document_id==doc))
            page.dirty = True
            session.commit()
        app.state.orchestrator.render(doc)
        with app.state.sessions() as session:
            child = session.get(Block, f'{doc}-p1-ocr1')
            assert child.source_text == '$$(192)$$'
            assert session.get(LayoutResult,child.id).fit_status == 'ocr_overlap_review_required'
