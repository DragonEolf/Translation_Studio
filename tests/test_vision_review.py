import json
from types import SimpleNamespace as N
from pathlib import Path
import httpx
import pymupdf as fitz
import pytest
from app.config import StudioError
from app.providers.legacy_settings import LegacyRepairSettings as Settings
from app.providers.vision import DeepSeekVisionReviewer, RepairProposal
from app.services.vision_repair import candidate_layout, review_page
from app.services.renderer import Renderer
from app.services.text_fitter import FontRegistry
from app.agents.checker import CheckerAgent


def proposal(**kw):
    return RepairProposal.model_validate({'block_id':'b1','reason':'Small text','actions':[{'type':'set_font_size','value':10}],**kw})


def test_provider_sends_two_images_and_enforces_json_schema():
    seen=[]
    def handler(request):
        body=json.loads(request.content);seen.append(body)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':proposal().model_dump_json()}}], 'usage':{'total_tokens':123}})
    settings=Settings(_env_file=None,openrouter_api_key='secret',studio_vision_timeout_seconds=1)
    reviewer=DeepSeekVisionReviewer(settings,httpx.MockTransport(handler))
    result,usage=reviewer.review({'block_id':'b1'},b'source',b'rendered')
    assert result.block_id=='b1' and usage=={'total_tokens':123}
    assert seen[0]['model'].startswith('deepseek/')
    assert len([v for v in seen[0]['messages'][1]['content'] if v['type']=='image_url'])==2
    assert 'untrusted data' in seen[0]['messages'][0]['content']
    assert seen[0]['max_tokens']==1200
    assert seen[0]['reasoning']=={'effort':'none'}


@pytest.mark.parametrize('answer,finish',[
    ('not json','stop'),
    (json.dumps({'block_id':'b1','actions':[{'type':'delete_image'}]}),'stop'),
    (json.dumps({'block_id':'wrong','actions':[]}), 'stop'),
    (json.dumps({'block_id':'b1','actions':[]}), 'length'),
])
def test_invalid_or_truncated_review_fails_closed(answer,finish):
    transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'choices':[{'finish_reason':finish,'message':{'content':answer}}]}))
    reviewer=DeepSeekVisionReviewer(Settings(_env_file=None,openrouter_api_key='secret'),transport)
    with pytest.raises(StudioError,match='Vision review failed'):
        reviewer.review({'block_id':'b1'},b'a',b'b')


def fixture(path):
    with fitz.open() as d:
        p=d.new_page(width=400,height=240);p.insert_text((30,65),'Source text',fontsize=12);d.save(path)
    b=N(id='b1',source_text='Source text',type='paragraph',bbox=[30,50,180,75],style={'size':12,'color':0,'source_regions':[[30,50,180,75]],'bold':False})
    t=N(text='Translated text',target_language='en',script='native',metadata_json={})
    l=N(bbox=[30,50,180,80],font='sans-serif',font_size=3,fit_status='fitted',line_breaks=[])
    page=N(width=400,height=240,number=1,metadata_json={'layout_mode':'paragraphs'})
    return (b,t,l),page


@pytest.mark.parametrize('actions',[
    [{'type':'set_font_size','value':1}],
    [{'type':'set_font_size','value':100}],
    [{'type':'set_bbox','value':[-100,20,150,75]}],
    [{'type':'set_bbox','value':[30,50,900,100]}],
    [{'type':'set_font_width','value':'imaginary'}],
    [{'type':'manual_review'},{'type':'set_font_size','value':10}],
    [{'type':'set_font_size','value':10},{'type':'set_font_size','value':9}],
])
def test_unsafe_actions_cannot_change_layout(tmp_path,actions):
    entry,page=fixture(tmp_path/'source.pdf')
    with pytest.raises(ValueError):
        candidate_layout(entry,proposal(actions=actions),[entry],[entry[0]],page,[],FontRegistry(),6)
    assert entry[2].font_size==3


def test_new_collisions_and_box_changes_in_preserve_mode_are_rejected(tmp_path):
    entry,page=fixture(tmp_path/'source.pdf')
    other=N(id='other',bbox=[185,50,210,80],style={})
    patch=proposal(actions=[{'type':'set_font_size','value':10},{'type':'set_bbox','value':[30,50,200,80]}])
    with pytest.raises(ValueError,match='overlap'):
        candidate_layout(entry,patch,[entry],[entry[0],other],page,[],FontRegistry(),6)
    with pytest.raises(ValueError,match='box'):
        candidate_layout(entry,patch,[entry],[entry[0]],page,[],FontRegistry(),6,allow_box=False)


def test_valid_repair_renders_checks_and_preserves_text(tmp_path):
    source=tmp_path/'source.pdf';entry,page=fixture(source);dest=tmp_path/'page.pdf'
    Renderer().render_page(source,1,[entry],dest)
    issues=CheckerAgent().run(source,dest,1,[entry]);stats={'calls':0,'accepted':0,'reviews':[],'usage':{}}
    class Reviewer:
        def review(self,context,a,b):
            assert a.startswith(b'\x89PNG') and b.startswith(b'\x89PNG')
            assert context['bbox']==[30,50,180,80]
            return proposal(),{'total_tokens':10}
    settings=Settings(_env_file=None,studio_vision_max_regions=1)
    checked=review_page(Reviewer(),settings,source,dest,page,[entry],[entry[0]],[],issues,FontRegistry(),stats)
    assert stats['calls']==stats['accepted']==1
    assert entry[2].font_size==10
    assert not any(i['code']=='missing_text' for i in checked)
    with fitz.open(dest) as d:assert 'Translated text' in d[0].get_text()
    assert not list(tmp_path.glob('*candidate*'))
    # Budget is per document, not reset by a later page.
    review_page(Reviewer(),settings,source,dest,page,[entry],[entry[0]],[],issues,FontRegistry(),stats)
    assert stats['calls']==1


def test_provider_outage_retains_existing_pdf_without_retrying_later_pages(tmp_path):
    source=tmp_path/'source.pdf';entry,page=fixture(source);dest=tmp_path/'page.pdf'
    Renderer().render_page(source,1,[entry],dest);before=dest.read_bytes()
    stats={'calls':0,'accepted':0,'reviews':[],'usage':{}}
    class Offline:
        def review(self,*args):raise StudioError('vision_review_unavailable','Unavailable',503)
    settings=Settings(_env_file=None)
    for _ in range(2):review_page(Offline(),settings,source,dest,page,[entry],[entry[0]],[],[],FontRegistry(),stats)
    assert stats['calls']==1 and stats['accepted']==0
    assert dest.read_bytes()==before


def test_repair_cannot_trade_a_collision_for_missing_text(tmp_path,monkeypatch):
    source=tmp_path/'source.pdf';entry,page=fixture(source);dest=tmp_path/'page.pdf'
    Renderer().render_page(source,1,[entry],dest);before=dest.read_bytes()
    stats={'calls':0,'accepted':0,'reviews':[],'usage':{}}
    class Reviewer:
        def review(self,*args):return proposal(),{}
    monkeypatch.setattr(CheckerAgent,'run',lambda *args:[{'code':'missing_text','block_id':'b1'}])
    review_page(Reviewer(),Settings(_env_file=None),source,dest,page,[entry],[entry[0]],[],
                [{'code':'collision','block_id':'b1'}],FontRegistry(),stats)
    assert stats['accepted']==0
    assert stats['reviews'][0]['reason']=='A rendering check became worse'
    assert entry[2].font_size==3 and dest.read_bytes()==before
