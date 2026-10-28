import copy
import re
import xml.etree.ElementTree as ET
from types import SimpleNamespace as N
from pathlib import Path
import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from app.config import Settings,StudioError
from app.main import create_app
from app.agents.block_maker import BlockMakerAgent
from app.agents.text_reader import TextReaderAgent
from app.agents.translator import TranslatorAgent
from app.services.rich_text import group_blocks,encode_groups,validate_groups,source_runs,export_xliff,XLIFF_NS
from test_studio import wait


def rich_block():
 styles={'f0':{'font':'Helvetica','size':14,'bold':False,'italic':False,'color':0},'f1':{'font':'Helvetica-Bold','size':14,'bold':True,'italic':False,'color':0}}
 runs=[{'id':'s0','text':'The total is ','style_id':'f0'},{'id':'s1','text':'1250.50 INR','style_id':'f1'},{'id':'s2','text':' for invoice INV-2048.','style_id':'f0'}]
 return N(id='b1',page_number=1,source_text=''.join(r['text'] for r in runs),style={'rich_text':{'base_style_id':'f0','styles':styles,'runs':runs}})


def source_pdf(path,second_line=False):
 with fitz.open() as d:
  p=d.new_page(width=500,height=230)
  x=30
  for text,font in [('Hello ','helv'),('bold phrase','hebo'),(' and plain text.','helv')]:
   p.insert_text((x,60),text,fontname=font,fontsize=14)
   x+=fitz.get_text_length(text,fontname=font,fontsize=14)
  if second_line:p.insert_text((30,78),'A second line continues the paragraph.',fontsize=14)
  p.draw_rect((30,140,160,170),color=(1,0,0))
  d.save(path)
 return path.read_bytes()


def test_grouping_retains_all_source_boxes_and_styles(tmp_path):
 path=tmp_path/'source.pdf';source_pdf(path,True);pages=TextReaderAgent().run(path);spans=BlockMakerAgent().run('d',pages);groups=group_blocks(spans,pages)
 assert len(groups)==1
 ir=groups[0]['style']['rich_text']
 assert len(ir['source_spans'])==len(spans)
 assert [s['bbox'] for s in ir['source_spans']]==[s['bbox'] for s in spans]
 assert 'bold phrase and plain text. A second line' in groups[0]['source_text']
 assert any(s['bold'] for s in ir['styles'].values())
 assert not groups[0]['style']['bold']


def test_columns_and_bullets_are_not_joined(tmp_path):
 path=tmp_path/'source.pdf'
 with fitz.open() as d:
  p=d.new_page(width=500,height=300)
  p.insert_text((30,60),'Left column',fontsize=12);p.insert_text((300,60),'Right column',fontsize=12)
  p.insert_text((30,100),'- First item',fontsize=12);p.insert_text((30,116),'- Second item',fontsize=12)
  d.save(path)
 pages=TextReaderAgent().run(path);groups=group_blocks(BlockMakerAgent().run('d',pages),pages)
 assert len(groups)==4
 assert all(not ('Left' in b['source_text'] and 'Right' in b['source_text']) for b in groups)


def test_rich_roundtrip_and_literal_style_ownership():
 b=rich_block();ids,payload=encode_groups([b],'Rtest');text,runs=validate_groups(payload,ids,[b])[0]
 assert text==b.source_text
 assert next(r['text'] for r in runs if r['style']['bold'])=='1250.50 INR'
 bad=payload.replace('1250.50 INR','total').replace('The total is ','1250.50 INR is ')
 with pytest.raises(StudioError):validate_groups(bad,ids,[b])


@pytest.mark.parametrize('change',[
 lambda x:x.replace('id="s1"','id="unknown"'),
 lambda x:x.replace('id="s1"','id="s1" style="bold"'),
 lambda x:x.replace('<span id="s1">1250.50 INR</span>','1250.50 INR'),
 lambda x:'unexpected'+x,
 lambda x:x.replace('1250.50 INR','<b>1250.50 INR</b>'),
 lambda x:'<!DOCTYPE x>'+x,
])
def test_rich_rejects_corrupt_markup(change):
 b=rich_block();ids,payload=encode_groups([b],'Rtest')
 with pytest.raises(StudioError):validate_groups(change(payload),ids,[b])


def test_discontinuous_style_codes_can_repeat_without_literal_duplication():
 b=rich_block();b.style['rich_text']['runs'][1]['text']='cats mice';b.source_text='The total is cats mice for invoice INV-2048.'
 ids,_=encode_groups([b],'Rtest')
 text,runs=validate_groups('<p id="Rtest_000">Do <span id="s1">cats</span> eat <span id="s1">mice</span>? INV-2048.</p>',ids,[b])[0]
 assert sum(r['style']['bold'] for r in runs)==2
 t=N(text=text,metadata_json={'rich_text':{'runs':runs}})
 root=ET.fromstring(export_xliff([(b,t)],'en'));ns={'x':XLIFF_NS}
 codes=root.findall('.//x:target/x:pc',ns)
 assert [c.get('id') for c in codes]==['s1','s1_copy1']
 assert all(c.get('dataRefStart')=='s1_start' for c in codes)


class Echo:
 def __init__(self):self.calls=[]
 async def translate(self,text,*args):
  self.calls.append(text)
  return {'translation':text.replace('Hello','Welcome'),'truncated':False,'usage':{'total_tokens':10}}


async def test_rich_provider_failure_splits_and_reports_style_fallback():
 class DropsTags(Echo):
  async def translate(self,text,*args):
   self.calls.append(text)
   if text.startswith('<p'):return {'translation':'bad','truncated':False}
   return {'translation':text,'truncated':False}
 p=DropsTags();b=rich_block();stats={}
 results=[x async for x in TranslatorAgent(p).translate_batch([b],'en','native',stats)]
 assert results[0][3] is None
 assert results[0][2]['rich_text']['fallback']
 assert results[0][1]==b.source_text
 assert next(r['text'].strip() for r in results[0][2]['rich_text']['runs'] if r['style']['bold'])=='1250.50 INR'
 assert stats['rich_fallbacks']==1


async def test_rich_auth_failure_does_not_fan_out():
 class Unauthorized(Echo):
  async def translate(self,*args):
   self.calls.append(args[0]);raise StudioError('invalid_api_key','Authentication failed',503)
 p=Unauthorized()
 with pytest.raises(StudioError):
  _=[x async for x in TranslatorAgent(p).translate_batch([rich_block(),rich_block()],'en','native',{})]
 assert len(p.calls)==1


@pytest.mark.parametrize("cache_enabled",[False,True])
def test_paragraph_pipeline_render_xliff_and_switch_back(tmp_path,cache_enabled):
 provider=Echo();data=source_pdf(tmp_path/'sample.pdf',True)
 fonts=Path('fonts').resolve()
 with TestClient(create_app(Settings(studio_data_dir=tmp_path/'data',studio_font_dir=fonts,studio_cache_enabled=cache_enabled),provider)) as client:
  upload=client.post('/v1/documents',files={'file':('sample.pdf',data)})
  doc=upload.json()['document_id'];wait(client,upload)
  job=wait(client,client.post(f'/v1/documents/{doc}/translations',json={'target_language':'en','layout_mode':'paragraphs'}))
  assert job['status']=='completed',job
  records=client.get(f'/v1/documents/{doc}/blocks').json()
  assert len(records)==1
  ir=records[0]['style']['rich_text'];translated=records[0]['translation']
  assert 'Welcome bold phrase and plain text. A second line' in translated['text']
  assert any(r['style']['bold'] for r in translated['metadata_json']['rich_text']['runs'])
  artifact=client.get(f'/v1/documents/{doc}/artifacts/final.pdf')
  with fitz.open(stream=artifact.content,filetype='pdf') as rendered:
   page=rendered[0];text=page.get_text()
   assert 'Welcome' in text and 'Hello' not in text and 'bold phrase' in text
   spans=[s for b in page.get_text('dict')['blocks'] if b['type']==0 for l in b['lines'] for s in l['spans']]
   assert any('bold phrase' in s['text'] and 'Bold' in s['font'] for s in spans)
   assert len(page.get_drawings())==1
  response=client.get(f'/v1/documents/{doc}/artifacts/translation.xlf');assert response.status_code==200
  root=ET.fromstring(response.content);assert root.get('version')=='2.2'
  assert root.find('.//{'+XLIFF_NS+'}target/{'+XLIFF_NS+'}pc').text=='bold phrase'
  edited=client.put(f'/v1/blocks/{records[0]["id"]}/translation',json={'text':'Plain edit'})
  assert edited.status_code==422
  job=wait(client,client.post(f'/v1/documents/{doc}/translations',json={'target_language':'en','layout_mode':'spans'}))
  assert job['status'] in {'completed','completed_with_issues'},job
  assert all(i['code']=='collision' for i in client.get(f'/v1/documents/{doc}/issues',params={'resolution':'manual_review_required'}).json())
  assert len(client.get(f'/v1/documents/{doc}/blocks').json())==4
  assert client.get(f'/v1/documents/{doc}/artifacts/translation.xlf').status_code==404
  repeated=wait(client,client.post(f'/v1/documents/{doc}/translations',json={'target_language':'en','layout_mode':'paragraphs'}))
  assert repeated['status']=='completed',repeated
  assert len(provider.calls)==(2 if cache_enabled else 3)


def test_layout_mode_is_validated(tmp_path):
 with TestClient(create_app(Settings(studio_data_dir=tmp_path),Echo())) as c:
  assert c.post('/v1/documents/none/translations',json={'target_language':'en','layout_mode':'whatever'}).status_code==422


def test_cell_space_allocations_do_not_overlap_and_keep_original_spans(tmp_path):
 path=tmp_path/'cells.pdf'
 with fitz.open() as d:
  p=d.new_page(width=420,height=230)
  for y in (30,65,130):p.draw_line((30,y),(390,y))
  for x in (30,210,390):p.draw_line((x,30),(x,130))
  for x,text in ((40,'Left heading'),(220,'Right heading')):p.insert_text((x,52),text,fontsize=12)
  for x in (40,220):
   p.insert_text((x,85),'- First item',fontsize=10)
   p.insert_text((x,100),'- Second item',fontsize=10)
  d.save(path)
 pages=TextReaderAgent().run(path);original=BlockMakerAgent().run('d',pages);groups=group_blocks(original,pages)
 assert len(groups)==6
 assert any(b['style'].get('layout_geometry')=='table_cell_space' for b in groups)
 for i,b in enumerate(groups):
  if b['style'].get('table_cell_bbox'):
   assert fitz.Rect(b['style']['table_cell_bbox']).contains(fitz.Rect(b['bbox']))
  assert all(fitz.Rect(b['bbox']).contains(fitz.Rect(s['bbox'])) for s in b['style']['rich_text']['source_spans'])
  for other in groups[i+1:]:assert (fitz.Rect(b['bbox'])&fitz.Rect(other['bbox'])).get_area()<=1


async def test_mixed_plain_and_rich_batches_are_separated():
 agent=TranslatorAgent(None)
 plain=N(id='plain',source_text='Hello',page_number=1)
 groups=list(agent.batches([plain,rich_block(),plain]))
 assert [len(g) for g in groups]==[1,1,1]


def test_format_hash_changes_when_style_changes():
 from app.services.rich_text import format_hash
 b=rich_block();before=format_hash(b)
 b.style['rich_text']['styles']['f1']['bold']=False
 assert format_hash(b)!=before


def test_superscript_reference_is_not_a_compound_identifier():
 from app.services.rich_text import literal_source
 b=rich_block();ir=b.style['rich_text']
 ir['styles']['f1'].update(size=6,bold=False,superscript=True)
 ir['runs'][0]['text']='GRE';ir['runs'][1]['text']='1';ir['runs'][2]['text']=' results.'
 b.source_text='GRE1 results.'
 assert literal_source(b)=='GRE 1  results.'
 ids,_=encode_groups([b],'Rtest')
 text,runs=validate_groups('<p id="Rtest_000">परीक्षा<span id="s1">1</span> परिणाम।</p>',ids,[b])[0]
 assert runs[1]['style']['superscript']
 with pytest.raises(StudioError):
  validate_groups('<p id="Rtest_000">परीक्षा<span id="s1">2</span> परिणाम।</p>',ids,[b])


def test_latin_acronym_with_superscript_keeps_reference_boundary_in_target():
 b=rich_block();ir=b.style['rich_text']
 ir['styles']['f1'].update(size=6,bold=False,superscript=True)
 ir['runs'][0]['text']='GRE';ir['runs'][1]['text']='1';ir['runs'][2]['text']=' results.'
 b.source_text='GRE1 results.'
 ids,_=encode_groups([b],'Rtest')
 text,runs=validate_groups('<p id="Rtest_000">GRE<span id="s1">1</span> results.</p>',ids,[b])[0]
 assert text=='GRE1 results.'
 assert next(r for r in runs if r.get('id')=='s1')['style']['superscript']


def test_center_alignment_uses_cell_center_before_box_expansion(tmp_path):
 path=tmp_path/'centered.pdf'
 with fitz.open() as doc:
  page=doc.new_page(width=400,height=200)
  page.draw_rect((20,20,180,180))
  for y,text in [(60,'Centered title'),(100,'Short')]:
   width=fitz.get_text_length(text,fontsize=12)
   page.insert_text((100-width/2,y),text,fontsize=12)
  doc.save(path)
 pages=TextReaderAgent().run(path)
 # Supply explicit cell geometry so this tests alignment independently of table detection.
 pages[0]['tables']=[{'cells':[{'bbox':[20,20,180,180]}]}]
 blocks=group_blocks(BlockMakerAgent().run('d',pages),pages)
 assert len(blocks)==2
 for block in blocks:
  assert block['style']['align']=='center'
  assert abs((block['bbox'][0]+block['bbox'][2])/2-100)<.01


def test_alignment_does_not_center_bullets_or_offset_lines():
 from app.services.rich_text import source_alignment
 page={'width':400,'height':200}
 line={'cell':(20,20,180,180),'bbox':[60,30,140,42],'text':'Title'}
 assert source_alignment([line],{'size':12},page)=='center'
 assert source_alignment([{**line,'text':'• Bullet'}],{'size':12},page)=='left'
 assert source_alignment([{**line,'bbox':[30,30,110,42]}],{'size':12},page)=='left'
