"""Local-only inline rendering trial using existing saved Tamil translations.
This script never creates an API client or makes network requests.
"""
import json,sqlite3,re
from pathlib import Path
from collections import defaultdict
from statistics import median
import pymupdf as fitz
from app.config import Settings
from app.services.text_fitter import FontRegistry
from scripts.inline_layout_experiment import fit_inline,paint,OUT

def main():
 OUT.mkdir(parents=True,exist_ok=True);did='1989c3d6538a453b80bf103e771da114';source=Path('data')/did/'source.pdf'
 db=sqlite3.connect('file:data/studio.sqlite3?mode=ro',uri=True);db.row_factory=sqlite3.Row
 rows=db.execute('select b.*,t.text,l.font_size from blocks b join translations t on t.block_id=b.id left join layouts l on l.block_id=b.id where b.document_id=? and b.page_number=1 and t.target_language="ta" and t.script="native" order by b.reading_order,t.revision desc',(did,)).fetchall()
 seen=set();groups=defaultdict(list)
 for r in rows:
  if r['id'] in seen:continue
  seen.add(r['id']);s=json.loads(r['style']);box=json.loads(r['bbox'])
  if not s.get('digital_span'):continue
  groups[re.sub(r'-s\d+$','',r['id'])].append({'id':r['id'],'text':r['text'],'source':r['source_text'],'bold':s.get('bold',False),'italic':s.get('italic',False),'color':s['color'],'bbox':box,'size':s['size'],'old_size':r['font_size']})
 chosen=[]
 for key,runs in groups.items():
  runs.sort(key=lambda s:s['bbox'][0]);x=min(s['bbox'][0] for s in runs)
  if len(runs)>=3 and x>90 and any(s['bold'] for s in runs) and any(not s['bold'] for s in runs):chosen.append((key,runs))
  if len(chosen)==10:break
 fonts=FontRegistry(Settings().studio_font_dir);records=[]
 with fitz.open(source) as src,fitz.open() as out:
  out.insert_pdf(src,from_page=0,to_page=0);p=out[0]
  for key,runs in chosen:
   for s in runs:p.add_redact_annot(s['bbox'],fill=False)
  p.apply_redactions(images=0,graphics=0,text=0)
  for key,runs in chosen:
   rect=fitz.Rect(runs[0]['bbox'])
   for s in runs[1:]:rect|=fitz.Rect(s['bbox'])
   # Existing translations were separate API calls: add explicit run separators.
   styled=[{**s,'text':s['text'].strip()+(' ' if i<len(runs)-1 else '')} for i,s in enumerate(runs)]
   size=fit_inline(styled,rect,max(s['size'] for s in runs),fonts,'ta',p.rect.width,p.rect.height)
   paint(p,styled,rect,size,fonts,'ta');records.append({'id':key,'bbox':list(rect),'spans':len(runs),'size':size,'old_sizes':[s['old_size'] for s in runs if s['old_size'] is not None],'text':''.join(s['text'] for s in styled)})
  out.save(OUT/'resume-tamil-inline-lines.pdf')
 with fitz.open(OUT/'resume-tamil-inline-lines.pdf') as out:
  page=out[0];tp=page.get_textpage();missing=[]
  for r in records:
   extracted=page.get_textbox(fitz.Rect(r['bbox'])+(-.1,-.1,.1,.1),textpage=tp)
   if re.sub(r'\s+','',r['text']) not in re.sub(r'\s+','',extracted):missing.append(r['id'])
 old=[x for r in records for x in r['old_sizes']];new=[r['size'] for r in records]
 report={'lines':len(records),'spans':sum(r['spans'] for r in records),'new_median_pt':median(new),'old_span_median_pt':median(old) if old else None,'new_line_sizes':new,'old_spans_below_6':sum(s<6 for s in old),'new_lines_below_6':sum(s<6 for s in new),'missing_semantic_lines':missing,'api_calls':0,'limitation':'Only selected lines replaced. Existing independent translations reused; no new joint translation or semantic paragraph detection.','lines_detail':records}
 (OUT/'resume-offline-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='lines_detail'},indent=2))
if __name__=='__main__':main()
