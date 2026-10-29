import json
import httpx
import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from app.config import Settings, StudioError
from app.main import create_app
from app.models import Translation
from app.providers.repair import AIRepairProvider, RepairSuggestion
from test_studio import FakeProvider, pdf_bytes, wait


class Repair:
    def __init__(self, text='Hi page 1', fail=False):
        self.calls=[]
        self.text=text
        self.fail=fail

    async def suggest(self, context, source, output):
        self.calls.append(context)
        assert source.startswith(b'\x89PNG') and output.startswith(b'\x89PNG')
        if self.fail:
            raise StudioError('repair_provider_unavailable','Offline',503)
        return RepairSuggestion(block_id=context['block_id'],action='repair',reason='Correct selected passage',text=self.text)


def ready(client):
    upload=client.post('/v1/documents',files={'file':('input.pdf',pdf_bytes(),'application/pdf')})
    identity=upload.json()['document_id']
    wait(client,upload)
    wait(client,client.post(f'/v1/documents/{identity}/translations',json={'target_language':'en'}))
    snapshot=client.get(f'/v1/documents/{identity}/review').json()
    body={'version':snapshot['version'],'request_id':'repair-one','region_ids':[snapshot['regions'][0]['id']], 'instruction':'Fix only this passage'}
    return identity,body


def test_selected_repair_publishes_checked_pdf_and_backup(tmp_path):
    provider=Repair()
    app=create_app(Settings(_env_file=None,studio_data_dir=tmp_path),FakeProvider(),repair_provider=provider)
    with TestClient(app) as client:
        identity,body=ready(client)
        before=(tmp_path/identity/'final.pdf').read_bytes()
        untouched=(tmp_path/identity/'page-2.pdf').read_bytes()
        response=client.post(f'/v1/documents/{identity}/review/repair',json=body)
        job=wait(client,response)
        assert job['status']=='completed_with_issues',job
        assert job['payload']['accepted']==1,job
        assert len(provider.calls)==1
        assert (tmp_path/identity/'previous.pdf').read_bytes()==before
        assert (tmp_path/identity/'page-2.pdf').read_bytes()==untouched
        with fitz.open(tmp_path/identity/'final.pdf') as pdf:
            assert 'Hi page 1' in pdf[0].get_text()
            assert 'Welcome page 2' in pdf[1].get_text()
        snapshot=client.get(f'/v1/documents/{identity}/review').json()
        assert snapshot['version']!=body['version'] and snapshot['previous_available']
        assert any(i['code']=='ai_repair_review_required' for i in snapshot['regions'][0]['issues'])
        repeated=client.post(f'/v1/documents/{identity}/review/repair',json=body)
        assert repeated.json()['job_id']==response.json()['job_id']
        assert len(provider.calls)==1
        assert client.post(f'/v1/documents/{identity}/review/repair',json={**body,'request_id':'stale'}).status_code==409
        assert client.get(f'/v1/documents/{identity}/artifacts/previous.pdf').content==before


@pytest.mark.parametrize('fail,text',[(False,'Hi page 99'),(True,'Hi page 1')])
def test_bad_repair_or_outage_keeps_pdf_and_revisions(tmp_path,fail,text):
    provider=Repair(text,fail)
    app=create_app(Settings(_env_file=None,studio_data_dir=tmp_path),FakeProvider(),repair_provider=provider)
    with TestClient(app) as client:
        identity,body=ready(client)
        before=(tmp_path/identity/'final.pdf').read_bytes()
        with app.state.sessions() as session:
            count=len(list(session.query(Translation)))
        job=wait(client,client.post(f'/v1/documents/{identity}/review/repair',json=body))
        assert job['status']==('failed' if fail else 'completed_with_issues'),job
        assert job['payload']['accepted']==0
        assert (tmp_path/identity/'final.pdf').read_bytes()==before
        with app.state.sessions() as session:
            assert len(list(session.query(Translation)))==count


def test_missing_configuration_and_invalid_regions(tmp_path):
    app=create_app(Settings(_env_file=None,studio_data_dir=tmp_path),FakeProvider())
    with TestClient(app) as client:
        identity,body=ready(client)
        assert client.post(f'/v1/documents/{identity}/review/repair',json=body).json()['error']['code']=='repair_not_configured'
    app=create_app(Settings(_env_file=None,studio_data_dir=tmp_path),FakeProvider(),repair_provider=Repair())
    with TestClient(app) as client:
        body['region_ids']=['another-document']
        assert client.post(f'/v1/documents/{identity}/review/repair',json=body).status_code==422


@pytest.mark.asyncio
async def test_provider_uses_source_output_and_rejects_truncation():
    requests=[]
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{}'}}]})
    provider=AIRepairProvider(Settings(_env_file=None,openrouter_api_key='secret'),httpx.MockTransport(handler))
    with pytest.raises(StudioError):
        await provider.suggest({'block_id':'one'},b'a',b'b')
    parts=requests[0]['messages'][1]['content']
    assert len([p for p in parts if p['type']=='image_url'])==2
    assert 'untrusted data' in requests[0]['messages'][0]['content']


def test_ocr_region_can_be_repaired_without_repeating_ocr(tmp_path):
    from test_ocr import FakeOCR, mixed_pdf
    ocr=FakeOCR()
    repair=Repair('Hi scan')
    app=create_app(Settings(_env_file=None,studio_data_dir=tmp_path,studio_ocr_dpi=72),
                   FakeProvider(),ocr_provider=ocr,repair_provider=repair)
    with TestClient(app) as client:
        upload=client.post('/v1/documents',files={'file':('scan.pdf',mixed_pdf())})
        identity=upload.json()['document_id'];wait(client,upload)
        wait(client,client.post(f'/v1/documents/{identity}/translations',json={'target_language':'en'}))
        snapshot=client.get(f'/v1/documents/{identity}/review').json()
        block=next(r for r in snapshot['regions'] if r['source_text']=='Hello scan')
        body={'version':snapshot['version'],'request_id':'ocr-repair','region_ids':[block['id']]}
        job=wait(client,client.post(f'/v1/documents/{identity}/review/repair',json=body))
        assert job['payload']['accepted']==1,job
        assert ocr.calls==1
        with fitz.open(tmp_path/identity/'final.pdf') as pdf:
            assert 'Hi scan' in pdf[1].get_text()


def test_formatted_repair_must_preserve_run_literals_and_styles(tmp_path):
    from types import SimpleNamespace as N
    from app.services.ai_repair import propose
    from app.services.text_fitter import FontRegistry
    with fitz.open() as doc:
        doc.new_page(width=400,height=200);doc.save(tmp_path/'source.pdf')
    runs=[{'text':'Total ','style':{'size':12,'color':0}},
          {'text':'1250 INR','style':{'size':12,'color':0,'bold':True}}]
    block=N(id='one',bbox=[20,20,300,70],source_text='Total 1250 INR',
            style={'size':12,'color':0,'rich_text':{'version':1,
              'styles':{'plain':runs[0]['style'],'bold':runs[1]['style']},
              'runs':[{'text':'Total ','style_id':'plain'},{'text':'1250 INR','style_id':'bold'}]}})
    translation=N(text='Total 1250 INR',target_language='en',script='native',metadata_json={'rich_text':{'runs':runs}})
    layout=N(bbox=[20,20,300,70],font='sans-serif',font_size=12,fit_status='fitted',line_breaks=[])
    page=N(width=400,height=200,number=1,source=tmp_path/'source.pdf')
    entry=(block,translation,layout)
    suggestion=RepairSuggestion(block_id='one',action='repair',reason='Correct wording',run_texts=['Sum ','1250 INR'])
    candidate=propose(entry,suggestion,[entry],[block],page,FontRegistry(),6)
    assert candidate[1].text=='Sum 1250 INR'
    assert candidate[1].metadata_json['rich_text']['runs'][1]['style']['bold']
    assert translation.text=='Total 1250 INR'
    with pytest.raises((ValueError,StudioError)):
        propose(entry,suggestion.model_copy(update={'run_texts':['Sum 1250 INR','amount']}),[entry],[block],page,FontRegistry(),6)
    with pytest.raises(ValueError):
        propose(entry,suggestion.model_copy(update={'text':'Flat text','run_texts':None}),[entry],[block],page,FontRegistry(),6)
