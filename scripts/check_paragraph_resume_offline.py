"""Local-only comparison on the saved resume; uses existing translations.

No API or network imports. Tests geometry and rich rendering, not joint
translation quality. User source/database remain unchanged.
"""
import json,re,sqlite3
from pathlib import Path
from types import SimpleNamespace as N
from statistics import median
from collections import Counter
import pymupdf as fitz
from app.config import Settings
from app.agents.text_reader import TextReaderAgent
from app.agents.block_maker import BlockMakerAgent
from app.agents.reorganiser import ReorganiserAgent
from app.agents.checker import CheckerAgent
from app.agents.translator import literal_only
from app.services.rich_text import group_blocks,source_runs
from app.services.text_fitter import FontRegistry
from app.services.renderer import Renderer

def main():
 did='1989c3d6538a453b80bf103e771da114';src=Path('data')/did/'source.pdf';out=Path('output/pdf/paragraph-pipeline');out.mkdir(parents=True,exist_ok=True)
 db=sqlite3.connect('file:data/studio.sqlite3?mode=ro',uri=True);db.row_factory=sqlite3.Row
 rows=db.execute('select b.id,b.source_text,t.text,l.font_size from blocks b join translations t on t.block_id=b.id left join layouts l on l.block_id=b.id where b.document_id=? and t.target_language="ta" and t.script="native" order by t.revision desc',(did,)).fetchall();translations={}
 for r in rows:translations.setdefault(r['id'],dict(r))
 pages=TextReaderAgent().run(src);raw_text={}
 for pn,page in enumerate(pages,1):
  for bi,raw in enumerate(b for b in page['blocks'] if b['type']==0):
   for li,line in enumerate(raw['lines']):
    for si,span in enumerate(line['spans']):raw_text[f'{did}-p{pn}-b{bi}-l{li}-s{si}']=span['text']
 spans=BlockMakerAgent().run(did,pages);groups=group_blocks(spans,pages);fonts=FontRegistry(Settings().studio_font_dir);reorganiser=ReorganiserAgent(fonts);entries=[];missing=[];old=[];new=[]
 for item in groups:
  b=N(**item);runs=[];failed=False
  if not b.style.get('rich_text'):continue
  for run in source_runs(b):
   parts=[]
   for sid in run['source_ids']:
    r=translations.get(sid)
    if r:
     raw=raw_text.get(sid,r['source_text'])
     parts.append((' ' if raw[:1].isspace() else '')+r['text'].strip()+(' ' if raw[-1:].isspace() else ''))
    else:
     source=next(s['text'] for s in b.style['rich_text']['source_spans'] if s['id']==sid)
     if literal_only(source):parts.append(source)
     else:failed=True;missing.append(sid)
   runs.append({**run,'text':''.join(parts)+(' ' if run['text'][-1:].isspace() and not ''.join(parts).endswith(' ') else '')})
  if failed:continue
  t=N(text=''.join(r['text'] for r in runs).strip(),target_language='ta',script='native',metadata_json={'rich_text':{'runs':runs,'fallback':True}})
  layout=N(**reorganiser.run(b,t,N(width=pages[0]['width'],height=pages[0]['height']),[]));entries.append((b,t,layout))
  for source in b.style['rich_text']['source_spans']:
   prior=translations.get(source['id'],{}).get('font_size')
   if prior is not None:
    old.append(prior);new.append(layout.font_size*source['style']['size']/b.style['size'])
 path=out/'resume-tamil-paragraphs-offline.pdf';Renderer(fonts).render_page(src,1,entries,path);issues=CheckerAgent().run(src,path,1,entries)
 report={'source_spans':len(spans),'layout_groups':len(groups),'rendered_groups':sum(l.fit_status=='fitted' for _,_,l in entries),'missing_source_translations':missing,'old_median_span_pt':median(old),'new_median_effective_span_pt':median(new),'old_spans_below_6':sum(x<6 for x in old),'new_spans_below_6':sum(x<6 for x in new),'checker_issues':dict(Counter(x['code'] for x in issues)),'issue_details':issues,'api_calls':0,'limitation':'Existing independent translations joined locally. This validates layout, not newly translated paragraph fluency.'}
 (out/'resume-offline-results.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
