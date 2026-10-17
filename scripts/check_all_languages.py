"""Live synthetic smoke test of all native-script targets; no user PDFs."""
import asyncio,json,time,unicodedata
from pathlib import Path
from types import SimpleNamespace as N
import pymupdf as fitz
from app.config import Settings
from app.providers.bodhan import BodhanProvider,LANGUAGES
from app.agents.translator import TranslatorAgent
from app.agents.reorganiser import ReorganiserAgent
from app.services.text_fitter import FontRegistry,SCRIPT_FONTS
from app.services.renderer import Renderer
from app.agents.checker import CheckerAgent

RANGES={'Devanagari':(0x900,0x97f),'Bengali':(0x980,0x9ff),'Gujarati':(0xa80,0xaff),'Gurmukhi':(0xa00,0xa7f),'Kannada':(0xc80,0xcff),'Malayalam':(0xd00,0xd7f),'Tamil':(0xb80,0xbff),'Telugu':(0xc00,0xc7f),'Oriya':(0xb00,0xb7f),'Arabic':(0x600,0x6ff),'OlChiki':(0x1c50,0x1c7f),'MeeteiMayek':(0xabc0,0xabff)}
async def main():
 settings=Settings();out=Path('artifacts/all-language-check');out.mkdir(parents=True,exist_ok=True)
 source=out/'source.pdf'
 with fitz.open() as doc:doc.new_page(width=600,height=400);doc.save(source)
 provider=BodhanProvider(settings.bodhan_api_key);agent=TranslatorAgent(provider)
 fitter=ReorganiserAgent(FontRegistry(settings.studio_font_dir or Path('fonts')))
 records=[]
 for lang in sorted(LANGUAGES):
  texts=['The meeting will take place tomorrow.','Please keep the original document safe.','Invoice INV-2048 costs 1250.50'] if lang!='en' else ['बैठक कल होगी।','कृपया मूल दस्तावेज़ सुरक्षित रखें।','चालान INV-2048 की राशि 1250.50 है।']
  blocks=[N(id=f'{lang}-{i}',page_number=1,source_text=t,bbox=[30,30+i*100,570,110+i*100],style={'size':14,'color':0,'fixed_box':True,'source_regions':[[30,30+i*100,570,110+i*100]]},confidence=1) for i,t in enumerate(texts)]
  stats={};start=time.perf_counter();record={'language':lang,'source':texts}
  try:
   results=[r async for r in agent.translate_batch(blocks,lang,'native',stats)]
   record['translations']=[t for _,t,_,_ in results];record['errors']=[e.code for _,_,_,e in results if e]
   entries=[]
   for b,t,_,error in results:
    if error:continue
    translation=N(text=t,target_language=lang,script='native')
    layout=N(**fitter.run(b,translation,N(width=600,height=400),[]))
    entries.append((b,translation,layout))
   record['fit_statuses']=[l.fit_status for _,_,l in entries]
   text=''.join(t or '' for t in record['translations'])
   if lang=='en':record['target_script_detected']=any(c.isascii() and c.isalpha() for c in text)
   else:
    low,high=RANGES[SCRIPT_FONTS[lang]]
    record['target_script_detected']=any(low<=ord(c)<=high and unicodedata.category(c).startswith('L') for c in text)
   path=out/f'{lang}.pdf';Renderer().render_page(source,1,entries,path)
   record['issues']=CheckerAgent().run(source,path,1,entries)
   record['passed']=len(results)==3 and not record['errors'] and record['fit_statuses']==['fitted']*3 and record['target_script_detected'] and not record['issues']
  except Exception as exc:
   record.update(passed=False,error_code=getattr(exc,'code',type(exc).__name__))
  record.update(stats=stats,seconds=round(time.perf_counter()-start,3));records.append(record)
  (out/'results.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
  print(lang,'PASS' if record['passed'] else 'FAIL',record.get('fit_statuses'),record.get('error_code',''),stats,flush=True)
 print('TOTAL:',sum(r['passed'] for r in records),'/',len(records),flush=True)
asyncio.run(main())
