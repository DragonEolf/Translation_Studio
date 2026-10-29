import json
import time
from pathlib import Path
from types import SimpleNamespace as N
import httpx
import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from app.config import Settings, StudioError
from app.main import create_app
from app.models import Translation
from app.providers.transliteration import BodhanTransliterationProvider
from app.services.transliteration import candidates, pronunciation_input, replace, validate_replacement


def test_candidates_protect_literals_units_domains_and_split_style_urls():
    text='VGF API ₹250 Cr 10 kg https://foo.com/API mail@api.com ABC123 example.com UPI.'
    t=N(text=text,metadata_json={})
    assert {i['word'] for i in candidates([(None,t)])}=={'VGF','API','UPI'}
    t.metadata_json={'rich_text':{'runs':[{'text':'https://foo.'},{'text':'com/API VGF'}]}}
    assert [i['word'] for i in candidates([(None,t)])]==['VGF']
    assert pronunciation_input('VGF')=='vee gee eff'
    assert pronunciation_input('Meesho')=='Meesho'


def test_replacement_keeps_rich_styles_and_protected_values():
    runs=[{'text':'VGF ','style':{'bold':True,'size':14},'id':'s1'},
          {'text':'API https://foo.com/VGF 12 kg','style':{'bold':False,'size':10}}]
    t=N(text=''.join(r['text'] for r in runs),metadata_json={'rich_text':{'runs':runs},'source_hash':'saved'})
    text,meta=replace(t,{'VGF':'வீ ஜி எஃப்'})
    assert text=='வீ ஜி எஃப் API https://foo.com/VGF 12 kg'
    assert meta['rich_text']['runs'][0]['style']==runs[0]['style']
    assert meta['rich_text']['runs'][0]['id']=='s1'
    assert t.metadata_json['rich_text']['runs']==runs
    assert meta['source_hash']=='saved'
    for value in ['VGF','<script>','123','', 'தமிழ் API']:
        with pytest.raises(StudioError): validate_replacement(value)


@pytest.mark.anyio
async def test_provider_uses_existing_key_and_documented_route():
    def handler(request):
        assert str(request.url)=='https://api.bodhan.ai/transliterate'
        assert request.headers['authorization']=='Bearer existing-key'
        assert json.loads(request.content)=={'text':'vee gee eff','language':'ta','script':'native'}
        return httpx.Response(200,json={'transliteration':'வீ ஜி எஃப்','truncated':False})
    p=BodhanTransliterationProvider('existing-key',transport=httpx.MockTransport(handler))
    assert (await p.transliterate('vee gee eff','ta'))['transliteration']=='வீ ஜி எஃப்'


class Echo:
    async def translate(self,text,language,script): return {'translation':text,'truncated':False}


class Pronunciations:
    def __init__(self): self.calls=[]
    async def transliterate(self,text,language):
        self.calls.append((text,language))
        return {'transliteration':'வீ ஜி எஃப்' if text=='vee gee eff' else 'ஏ பி ஐ'}


def wait(client,result):
    assert result.status_code==202,result.text
    for _ in range(200):
        job=client.get('/v1/jobs/'+result.json()['job_id']).json()
        if job['status'] in {'completed','completed_with_issues','failed','cancelled'}: return job
        time.sleep(.02)
    pytest.fail('Timed out')


def setup_document(client):
    with fitz.open() as doc:
        p=doc.new_page(width=500,height=250)
        p.insert_text((30,55),'VGF API 250 INR https://example.com',fontsize=12)
        data=doc.tobytes()
    response=client.post('/v1/documents',files={'file':('sample.pdf',data,'application/pdf')})
    doc_id=response.json()['document_id']; assert wait(client,response)['status']=='completed'
    translated=wait(client,client.post(f'/v1/documents/{doc_id}/translations',json={'target_language':'ta'}))
    assert translated['status'] in {'completed','completed_with_issues'},translated
    return doc_id


def test_preview_then_apply_only_selected_words_and_idempotent_retry(tmp_path):
    p=Pronunciations()
    app=create_app(Settings(_env_file=None,studio_data_dir=tmp_path,studio_font_dir=Path('fonts').resolve()),provider=Echo(),transliteration_provider=p)
    with TestClient(app) as client:
        doc=setup_document(client)
        before=(tmp_path/doc/'final.pdf').read_bytes()
        offered=client.get(f'/v1/documents/{doc}/transliteration-candidates').json()['candidates']
        assert {i['word'] for i in offered}=={'VGF','API'}
        bad=client.post(f'/v1/documents/{doc}/transliteration-preview',json={'words':['INR']})
        assert bad.status_code==422 and not p.calls
        preview=wait(client,client.post(f'/v1/documents/{doc}/transliteration-preview',json={'words':['VGF','API'],'request_id':'preview-once'}))
        assert preview['status']=='completed'
        retry=client.post(f'/v1/documents/{doc}/transliteration-preview',json={'words':['VGF','API'],'request_id':'preview-once'})
        assert retry.json()['job_id']==preview['id']
        assert (tmp_path/doc/'final.pdf').read_bytes()==before
        assert p.calls==[('vee gee eff','ta'),('ay pee eye','ta')]
        body={'preview_job_id':preview['id'],'words':['VGF']}
        response=client.post(f'/v1/documents/{doc}/transliteration-apply',json=body)
        applied=wait(client,response)
        assert applied['status'] in {'completed','completed_with_issues'},applied
        with fitz.open(tmp_path/doc/'final.pdf') as pdf:
            text=pdf[0].get_text()
            assert 'வீ ஜி எஃப்' in text and 'API' in text
            assert '250 INR https://example.com' in text
        repeat=client.post(f'/v1/documents/{doc}/transliteration-apply',json=body)
        assert repeat.json()['job_id']==response.json()['job_id']
        assert len(p.calls)==2


def test_apply_rejects_stale_preview(tmp_path):
    app=create_app(Settings(_env_file=None,studio_data_dir=tmp_path,studio_font_dir=Path('fonts').resolve()),provider=Echo(),transliteration_provider=Pronunciations())
    with TestClient(app) as client:
        doc=setup_document(client)
        preview=wait(client,client.post(f'/v1/documents/{doc}/transliteration-preview',json={'words':['VGF']}))
        with app.state.sessions() as session:
            translation=session.query(Translation).first()
            translation.text+=' changed';session.commit()
        result=client.post(f'/v1/documents/{doc}/transliteration-apply',json={'preview_job_id':preview['id'],'words':['VGF']})
        assert result.status_code==409 and result.json()['error']['code']=='stale_transliteration_preview'


@pytest.mark.anyio
async def test_missing_bodhan_route_switches_to_phonetic_fallback_and_skips_repeated_404s():
    primary=[]
    def handler(request):primary.append(request);return httpx.Response(404,json={'detail':'Not Found'})
    fallback=Pronunciations()
    p=BodhanTransliterationProvider('key',transport=httpx.MockTransport(handler),fallback=fallback)
    assert (await p.transliterate('vee gee eff','ta'))['transliteration']=='வீ ஜி எஃப்'
    await p.transliterate('ay pee eye','ta')
    assert len(primary)==1 and len(fallback.calls)==2


@pytest.mark.anyio
async def test_404_without_fallback_is_actionable_and_auth_errors_never_fallback():
    for status,code in [(404,'transliteration_endpoint_unavailable'),(401,'transliteration_authentication_failed')]:
        fallback=Pronunciations() if status==401 else None
        p=BodhanTransliterationProvider('key',transport=httpx.MockTransport(lambda _:httpx.Response(status)),fallback=fallback)
        with pytest.raises(StudioError) as error:await p.transliterate('API','ta')
        assert error.value.code==code
        if fallback:assert fallback.calls==[]


@pytest.mark.anyio
@pytest.mark.parametrize('value,finish',[('கம்ப்யூட்டர்','stop'),('கணினி','length'),('computer','stop'),('कम्प्यूटर','stop')])
async def test_phonetic_fallback_validates_completion_and_target_script(value,finish):
    from app.providers.transliteration import OpenRouterPhoneticProvider
    requests=[]
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':finish,'message':{'content':json.dumps({'transliteration':value})}}]})
    p=OpenRouterPhoneticProvider('secret',transport=httpx.MockTransport(handler))
    if value=='கம்ப்யூட்டர்' and finish=='stop':
        assert (await p.transliterate('computer','ta'))['provider']=='openrouter-phonetic'
    else:
        with pytest.raises(StudioError):await p.transliterate('computer','ta')
    assert requests[0]['model']=='openai/gpt-4.1-mini'
    assert 'not meaning' in requests[0]['messages'][0]['content']
    assert json.loads(requests[0]['messages'][1]['content'])['text']=='computer'
