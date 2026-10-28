"""Live synthetic-only end-to-end test of the app's paragraph layout mode.

Creates its own test PDF. Never opens user uploads or the production database.
"""
import argparse,json,time
from pathlib import Path
import pymupdf as fitz
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.models import LayoutResult
from app.providers.bodhan import BodhanProvider

OUT=Path('output/pdf/paragraph-pipeline')

def source_pdf(path):
 paragraphs=[
  [[('We built a ','helv'),('translation system','hebo'),(' that supports','helv')],[('multiple languages and keeps ','helv'),('original formatting','heit'),(' intact.','helv')]],
  [[('Please ','helv'),('do not delete','hebo'),(' the original document.','helv')],[('Save the translated copy and keep a backup for reference.','helv')]],
  [[('The total is ','helv'),('1250.50 INR','hebo'),(' for invoice INV-2048.','helv')],[('Please check the amount before approving the payment.','helv')]],
  [[('Click ','helv'),('Save','hebo'),(' and keep a ','helv'),('backup copy','hebo'),(' of this document.','helv')],[('You can download the translated PDF after processing.','helv')]],
 ]
 with fitz.open() as d:
  p=d.new_page(width=600,height=560)
  for index,lines in enumerate(paragraphs):
   y=65+index*120
   p.draw_line((35,y-25),(565,y-25),color=(.75,.8,.85))
   for li,runs in enumerate(lines):
    x=35
    for text,font in runs:
     p.insert_text((x,y+li*16),text,fontname=font,fontsize=12,color=(0,0,.6) if font=='heit' else (0,0,0))
     x+=fitz.get_text_length(text,fontname=font,fontsize=12)
  d.save(path)
 return path.read_bytes()

def wait(client,response):
 if response.status_code!=202:raise RuntimeError(response.text)
 deadline=time.monotonic()+240
 while time.monotonic()<deadline:
  job=client.get('/v1/jobs/'+response.json()['job_id']).json()
  if job['status'] in {'completed','completed_with_issues','failed'}:return job
  time.sleep(.2)
 raise RuntimeError('job timeout')

def main():
 OUT.mkdir(parents=True,exist_ok=True);source=OUT/'source.pdf';data=source_pdf(source);settings=Settings();records=[]
 for language in ['hi','ta']:
  app=create_app(Settings(studio_data_dir=OUT/language,studio_font_dir=settings.studio_font_dir),BodhanProvider(settings.bodhan_api_key))
  started=time.perf_counter()
  with TestClient(app) as client:
   uploaded=client.post('/v1/documents',files={'file':('Paragraph formatting demo.pdf',data)})
   did=uploaded.json()['document_id'];inspection=wait(client,uploaded)
   job=wait(client,client.post(f'/v1/documents/{did}/translations',json={'target_language':language,'layout_mode':'paragraphs'}))
   blocks=client.get(f'/v1/documents/{did}/blocks').json();issues=client.get(f'/v1/documents/{did}/issues').json()
   for name in ['final.pdf','translation.xlf','structure.json']:
    response=client.get(f'/v1/documents/{did}/artifacts/{name}')
    if response.status_code==200:(OUT/f'{language}-{name}').write_bytes(response.content)
   with app.state.sessions() as session:
    sizes=[session.get(LayoutResult,b['id']).font_size for b in blocks if session.get(LayoutResult,b['id'])]
   row=dict(language=language,seconds=round(time.perf_counter()-started,2),inspection=inspection['status'],status=job['status'],job_id=job['id'],stats=job['payload'].get('translation_batch_stats'),result=job['payload'].get('result'),issues=[x for x in issues if x['resolution']!='resolved'],paragraphs=len(blocks),original_spans=sum(len(b['style'].get('rich_text',{}).get('source_spans',[])) for b in blocks),font_sizes=sizes,translations=[{'source':b['source_text'],'target':b['translation']['text'] if b.get('translation') else None,'runs':b['translation']['metadata_json'].get('rich_text',{}).get('runs') if b.get('translation') else None} for b in blocks])
   records.append(row);(OUT/'results.json').write_text(json.dumps(records,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in row.items() if k!='translations'}),flush=True)
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
 if not args.live:parser.error('--live is required')
 main()
