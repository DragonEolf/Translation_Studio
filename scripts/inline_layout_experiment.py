"""Synthetic-only live test of two-level IDs and inline PDF formatting.
No document/database access. Run with --live to call the configured Bodhan API.
"""
import argparse,asyncio,html,json,time
import xml.etree.ElementTree as ET
from pathlib import Path
import pymupdf as fitz
from app.config import Settings
from app.providers.bodhan import BodhanProvider
from app.agents.translator import validate_literals
from app.services.text_fitter import FontRegistry
OUT=Path('output/pdf/inline-layout')

def group(identity,texts,bold=None):
 return {'id':identity,'spans':[{'id':f'{identity}_s{i:02}_{"b" if (bold[i] if bold else i%2) else "r"}','text':t,'bold':bool(bold[i] if bold else i%2)} for i,t in enumerate(texts)]}

def synthetic():
 return [group('P01',['We built a ','translation system',' that supports multiple languages.']),group('P02',['Please ','do not delete',' the original document.']),group('P03',['The total is ','1250.50 INR',' for invoice INV-2048.']),group('P04',['Use ','A & B <draft>',' as the label, then save the report.']),group('P05',['The meeting scheduled for tomorrow ','has been postponed',' until Friday.']),group('P06',['Click ','Save',' and keep a ','backup copy',' of this document.'])]

def encode(groups):
 return '\n'.join('<p id="'+g['id']+'">'+''.join('<span id="'+s['id']+'">'+html.escape(s['text'],quote=False)+'</span>' for s in g['spans'])+'</p>' for g in groups)

def validate(output,groups):
 if not isinstance(output,str) or '<!' in output or '<?' in output:raise ValueError('unsafe XML')
 root=ET.fromstring('<batch>'+output+'</batch>')
 if (root.text or '').strip() or len(root)!=len(groups):raise ValueError('paragraph count/outside text')
 expected={g['id']:g for g in groups};result={}
 for p in root:
  pid=p.get('id')
  if p.tag!='p' or p.attrib!={'id':pid} or pid not in expected or pid in result or (p.text or '').strip() or (p.tail or '').strip():raise ValueError('paragraph identity/content')
  g=expected[pid];styles={s['id']:s for s in g['spans']};runs=[];seen=set()
  for s in p:
   sid=s.get('id')
   if s.tag!='span' or s.attrib!={'id':sid} or sid not in styles or sid in seen or len(s) or (s.tail or '').strip() or not (s.text or '').strip():raise ValueError('span identity/content')
   seen.add(sid);runs.append({**styles[sid],'text':s.text})
  if seen!=set(styles):raise ValueError('missing spans')
  validate_literals(''.join(s['text'] for s in g['spans']),''.join(s['text'] for s in runs))
  result[pid]=runs
 return result

def markup(runs,size,fonts,lang):
 faces=[];parts=[]
 for bold in (False,True):
  path=Path(fonts.resolve(lang,'native',bold));name='nb' if bold else 'nr'
  faces.append(f"@font-face{{font-family:{name};src:url('{path.as_posix()}')}}")
 for s in runs:
  name='nb' if s.get('bold') else 'nr'
  parts.append(f'<span style="font-family:{name};color:#{s.get("color",0):06x};font-style:{"italic" if s.get("italic") else "normal"}">{html.escape(s["text"])}</span>')
 return '<div>'+''.join(parts)+'</div>',''.join(faces)+f'*{{margin:0;padding:0}} div{{font-size:{size}pt;line-height:1.05}}'

def fit_inline(runs,rect,original,fonts,lang,w,h):
 low,high,best=0.,original,None
 for attempt in range(16):
  size=original if attempt==0 else (low+high)/2;content,css=markup(runs,size,fonts,lang)
  with fitz.open() as d:
   p=d.new_page(width=w,height=h);spare,_=p.insert_htmlbox(rect,content,css=css,scale_low=1)
  if spare>=0:
   best=size;low=size
   if attempt==0:break
  else:high=size
 if best is None:raise ValueError('inline overflow')
 return best

def paint(page,runs,rect,size,fonts,lang):
 content,css=markup(runs,size,fonts,lang)
 with fitz.open() as d:
  p=d.new_page(width=page.rect.width,height=page.rect.height)
  spare,_=p.insert_htmlbox(rect,content,css=css,scale_low=1)
  if spare<0:raise ValueError('render overflow')
  svg=p.get_svg_image(text_as_path=True)
  with fitz.open('svg',svg.encode()) as vector:
   with fitz.open('pdf',vector.convert_to_pdf()) as shaped:page.show_pdf_page(page.rect,shaped,0)
 # Same selectable semantic layer as the app; visible Indic glyphs are outlines.
 text=''.join(s['text'] for s in runs);rect=fitz.Rect(rect);size=max(.5,rect.height/1.374)
 origin=fitz.Point(rect.x0,rect.y0+1.075*size);stretch=rect.width*.999/fitz.get_text_length('x',fontsize=size)
 before=set(page.get_contents());page.insert_text(origin,'x',fontsize=size,render_mode=3,morph=(origin,fitz.Matrix(stretch,1)))
 actual='FEFF'+text.encode('utf-16-be').hex().upper()
 for x in set(page.get_contents())-before:page.parent.update_stream(x,b'/Span << /ActualText <'+actual.encode()+b'> >> BDC\n'+page.parent.xref_stream(x)+b'\nEMC')

async def main():
 OUT.mkdir(parents=True,exist_ok=True);settings=Settings();provider=BodhanProvider(settings.bodhan_api_key);fonts=FontRegistry(settings.studio_font_dir);records=[];gs=synthetic()
 with fitz.open() as demo:
  for lang in ['hi','ta']:
   payload=encode(gs);start=time.perf_counter();r=await provider.translate(payload,lang,'native')
   record={'language':lang,'input':payload,'output':r['translation'],'seconds':round(time.perf_counter()-start,2),'truncated':r['truncated']};records.append(record)
   try:
    if r['truncated']:raise ValueError('truncated')
    translated=validate(r['translation'],gs);record.update(valid=True,paragraphs=len(translated),spans=sum(len(x) for x in translated.values()))
   except Exception as e:record.update(valid=False,error=str(e));translated={}
   (OUT/'synthetic-api-results.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
   p=demo.new_page(width=620,height=760);p.insert_text((25,25),'Nested IDs + inline formatting: '+lang,fontsize=16);sizes=[]
   for i,g in enumerate(gs):
    y=55+i*110;p.insert_text((25,y),''.join(s['text'] for s in g['spans']),fontsize=9)
    if g['id'] not in translated:continue
    rect=fitz.Rect(25,y+12,590,y+88);runs=translated[g['id']];size=fit_inline(runs,rect,14,fonts,lang,620,760);sizes.append(size);paint(p,runs,rect,size,fonts,lang)
   record['font_sizes']=sizes
  demo.save(OUT/'inline-formatting-demo.pdf')
 (OUT/'synthetic-api-results.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
 print(json.dumps([{k:v for k,v in r.items() if k not in {'input','output'}} for r in records],indent=2))
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
 if not args.live:parser.error('--live required')
 asyncio.run(main())
