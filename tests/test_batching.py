import html
from types import SimpleNamespace
import pytest
import pymupdf as fitz
from fastapi.testclient import TestClient
from app.agents.translator import TranslatorAgent, encode_batch, validate_batch
from app.config import Settings, StudioError
from app.main import create_app
from test_studio import wait


def blocks(texts, page=1):
    return [SimpleNamespace(id=str(i),source_text=t,page_number=page) for i,t in enumerate(texts)]


def test_xml_roundtrip_decodes_entities_once_and_preserves_special_characters():
    items=blocks(['A & B <draft> "quotes" | \\ Ω ±', 'Literal &amp; and <span id="other">text</span>\nNext line'])
    ids,payload=encode_batch(items,"batch")
    assert validate_batch(payload,ids,items)==[b.source_text for b in items]


@pytest.mark.parametrize("output",[
    '<span id="a">Hello</span>',  # missing ID
    '<span id="a">Hello</span><span id="a">World</span>',
    '<span id="b">World</span><span id="a">Hello</span>',
    '<span id="a">Hello</span><span id="unknown">World</span>',
    '<span id="a">Hello</span><span id="b">World</span><span id="c">Extra</span>',
    'Unparsed<span id="a">Hello</span><span id="b">World</span>',
    '<span id="a">Hello</span>Unparsed<span id="b">World</span>',
    '<span id="a">Hello</span><span id="b">World</span>Unparsed',
    '<span id="a"><b>Hello</b></span><span id="b">World</span>',
    '<span id="a" style="x">Hello</span><span id="b">World</span>',
    '<span id="a"> </span><span id="b">World</span>',
    '<span id="a">A & B</span><span id="b">World</span>',
    '<span id="a">Hello<span id="b">World</span>',
    '<!-- comment --><span id="a">Hello</span><span id="b">World</span>',
    '<?xml version="1.0"?><span id="a">Hello</span><span id="b">World</span>',
    '<!DOCTYPE batch><span id="a">Hello</span><span id="b">World</span>',
    None,
])
def test_invalid_batch_is_rejected(output):
    with pytest.raises(StudioError,match="invalid tags"):
        validate_batch(output,["a","b"],blocks(["Hello","World"]))


def test_literals_must_stay_in_their_own_span():
    items=blocks(['Invoice INV-2048 costs 1,250.50', 'Visit https://example.org/?x=7&y=8'])
    ids,payload=encode_batch(items,"batch")
    assert validate_batch(payload,ids,items)==[b.source_text for b in items]
    swapped=''.join(f'<span id="{i}">{html.escape(b.source_text)}</span>' for i,b in zip(ids,reversed(items)))
    with pytest.raises(StudioError): validate_batch(swapped,ids,items)


def test_batch_bounds_use_escaped_length_and_page_boundaries():
    agent=TranslatorAgent(None)
    items=blocks(['&'*60]*4)+blocks(['Other page'],page=2)
    batches=list(agent.batches(items,size=3,max_chars=700))
    assert [len(b) for b in batches]==[2,2,1]
    assert all(len(encode_batch(b,"B"+"0"*12)[1])<=700 for b in batches)
    assert [len(b) for b in agent.batches(items,size=1)]==[1]*5


class Echo:
    def __init__(self): self.calls=[]
    async def translate(self,text,*args):
        self.calls.append(text)
        return {"translation":text.replace("Hello","Welcome"),"usage":{"total_tokens":10},"truncated":False}


async def test_valid_batch_uses_one_call_and_counts_usage_once():
    provider=Echo();agent=TranslatorAgent(provider)
    items=blocks(['Hello & <one>', 'Hello two 42'])
    results=[r async for r in agent.translate_batch(items,"en","native")]
    assert len(provider.calls)==1
    assert [r[1] for r in results]==['Welcome & <one>','Welcome two 42']
    assert all(r[3] is None for r in results)
    assert sum(r[2]['usage'].get('total_tokens',0) for r in results)==10
    assert len({r[2]['batch_id'] for r in results})==1


@pytest.mark.parametrize('failure',['malformed','truncated','changed_literal'])
async def test_invalid_batches_split_then_fall_back_to_single_spans(failure):
    class Broken(Echo):
        async def translate(self,text,*args):
            self.calls.append(text)
            if text.startswith('<span'):
                return {'translation':text.replace('42','43') if failure=='changed_literal' else 'bad', 'truncated':failure=='truncated'}
            return {'translation':text.replace('Hello','Welcome'),'truncated':False}
    provider=Broken();items=blocks(['Hello one 42','Hello two 42','Hello three 42','Hello four 42'])
    stats={}
    results=[r async for r in TranslatorAgent(provider).translate_batch(items,'en','native',stats)]
    assert [r[1] for r in results]==[b.source_text.replace('Hello','Welcome') for b in items]
    assert stats['invalid_batches']==3
    assert stats['individual_calls']==4
    assert len(provider.calls)==7


async def test_auth_errors_do_not_trigger_splitting():
    class Unauthorized(Echo):
        async def translate(self,text,*args):
            self.calls.append(text)
            raise StudioError('invalid_api_key','Authentication failed',503)
    provider=Unauthorized()
    with pytest.raises(StudioError,match='Authentication'):
        _=[r async for r in TranslatorAgent(provider).translate_batch(blocks(['one','two']),'en','native')]
    assert len(provider.calls)==1


@pytest.mark.parametrize('cache_enabled',[False,True])
def test_pipeline_batches_skips_literals_and_honors_cache_setting(tmp_path,cache_enabled):
    provider=Echo()
    app=create_app(Settings(studio_data_dir=tmp_path,studio_font_dir=None,studio_cache_enabled=cache_enabled),provider)
    with fitz.open() as pdf:
        page=pdf.new_page()
        for y,text in [(60,'Hello alpha'),(90,'Hello beta'),(120,'12.50'),(150,'Hello <draft> & more')]:
            page.insert_text((30,y),text,fontsize=14)
        data=pdf.tobytes()
    with TestClient(app) as client:
        upload=client.post('/v1/documents',files={'file':('batch.pdf',data)})
        doc=upload.json()['document_id'];wait(client,upload)
        result=wait(client,client.post(f'/v1/documents/{doc}/translations',json={'target_language':'en'}))
        assert result['status']=='completed',result
        assert len(provider.calls)==1
        assert result['payload']['translation_batch_stats']['batch_calls']==1
        records=client.get(f'/v1/documents/{doc}/blocks').json()
        assert len(records)==4
        assert all(r['translation']['text']==r['source_text'].replace('Hello','Welcome') for r in records)
        with fitz.open(stream=client.get(f'/v1/documents/{doc}/artifacts/final.pdf').content,filetype='pdf') as pdf:
            assert 'Hello' not in pdf[0].get_text()
            assert 'Welcome' in pdf[0].get_text()
        wait(client,client.post(f'/v1/documents/{doc}/translations',json={'target_language':'en'}))
        assert len(provider.calls)==(1 if cache_enabled else 2)


def test_failed_fresh_translation_does_not_render_old_cached_results(tmp_path):
    class Provider(Echo):
        fail=False
        async def translate(self,text,*args):
            if self.fail:
                return {'translation':'partial','truncated':True}
            return await super().translate(text,*args)
    provider=Provider()
    app=create_app(Settings(studio_data_dir=tmp_path,studio_font_dir=None),provider)
    with fitz.open() as pdf:
        page=pdf.new_page();page.insert_text((30,60),'Hello world',fontsize=14)
        data=pdf.tobytes()
    with TestClient(app) as client:
        upload=client.post('/v1/documents',files={'file':('fresh.pdf',data)})
        doc=upload.json()['document_id'];wait(client,upload)
        assert wait(client,client.post(f'/v1/documents/{doc}/translations',json={'target_language':'en'}))['status']=='completed'
        provider.fail=True
        failed=wait(client,client.post(f'/v1/documents/{doc}/translations',json={'target_language':'en'}))
        assert failed['status']=='failed'
        assert failed['error_code']=='reconstruction_failed'
        assert client.get(f'/v1/documents/{doc}/artifacts/final.pdf').status_code==404
        with fitz.open(stream=client.get(f'/v1/documents/{doc}/artifacts/draft.pdf').content,filetype='pdf') as pdf:
            assert 'Hello world' in pdf[0].get_text()
            assert 'Welcome' not in pdf[0].get_text()
