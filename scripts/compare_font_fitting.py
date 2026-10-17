"""Experimental fixed-box shared-size comparison on the saved Tamil resume.

Uses saved translations without API calls or database writes. Source PDF block
IDs are grouping candidates, not semantic paragraph detection. Does not change
production fitting. Run from the repository root with PYTHONPATH=.
"""
import json,sqlite3,re,statistics
from pathlib import Path
from types import SimpleNamespace as N
from collections import defaultdict
import pymupdf as fitz
from app.config import Settings
from app.agents.reorganiser import ReorganiserAgent
from app.services.text_fitter import FontRegistry,markup
from app.services.renderer import Renderer
from app.agents.checker import CheckerAgent
out=Path('output/pdf');did='1989c3d6538a453b80bf103e771da114';source=Path('data')/did/'source.pdf'
c=sqlite3.connect('file:data/studio.sqlite3?mode=ro',uri=True);c.row_factory=sqlite3.Row
rows=c.execute('select b.*,t.text,t.target_language,t.script from blocks b join translations t on b.id=t.block_id where b.document_id=? and b.page_number=1 order by b.reading_order,t.revision desc',(did,)).fetchall()
fitter=ReorganiserAgent(FontRegistry(Settings().studio_font_dir));entries=[];seen=set()
with fitz.open(source) as doc: w,h=doc[0].rect.width,doc[0].rect.height
for r in rows:
 if r['id'] in seen:continue
 seen.add(r['id']);style=json.loads(r['style'])
 if not style.get('digital_span'):continue
 b=N(id=r['id'],bbox=json.loads(r['bbox']),source_text=r['source_text'],style=style,confidence=r['confidence'])
 t=N(text=r['text'],target_language=r['target_language'],script=r['script'])
 l=N(**fitter.run(b,t,N(width=w,height=h),[]));entries.append((b,t,l))
Renderer().render_page(source,1,entries,out/'resume-tamil-current-fitting.pdf')
groups=defaultdict(list)
for i,(b,t,l) in enumerate(entries):
 # Source PDF text block is only a grouping candidate, not a guaranteed paragraph.
 groups[(re.sub(r'-l\d+-s\d+$','',b.id),b.style['size'])].append(i)
shared=[]
for b,t,l in entries: shared.append((b,t,N(**vars(l))))
for ids in groups.values():
 size=min(entries[i][2].font_size for i in ids)
 for i in ids:shared[i][2].font_size=size
Renderer().render_page(source,1,shared,out/'resume-tamil-shared-fitting.pdf')
# Reuse the text page for each output during this read-only checker pass.
original_get_textbox=fitz.Page.get_textbox
textpages={}
def cached_textbox(page,rect,*args,**kwargs):
 key=(page.parent.name,page.number)
 if key not in textpages: textpages[key]=page.get_textpage()
 return original_get_textbox(page,rect,textpage=textpages[key])
fitz.Page.get_textbox=cached_textbox
results={}
for name,es in [('current',entries),('shared',shared)]:
 sizes=[l.font_size for b,t,l in es];issues=CheckerAgent().run(source,out/f'resume-tamil-{ "current" if name=="current" else "shared"}-fitting.pdf',1,es)
 from collections import Counter
 results[name]={'spans':len(es),'fitted':sum(l.fit_status=='fitted' for b,t,l in es),'minimum_pt':min(sizes),'median_pt':statistics.median(sizes),'under_6pt':sum(s<6 for s in sizes),'issues':dict(Counter(x['code'] for x in issues)),'groups_with_inconsistent_sizes':sum(max(es[i][2].font_size for i in ids)-min(es[i][2].font_size for i in ids)>.1 for ids in groups.values() if len(ids)>1)}
results['grouping']='Source PDF block and original font size; prototype, no semantic paragraph detection or reflow.'
results['groups']=len(groups);results['spans_shrunk_further']=sum(n.font_size<o.font_size-.01 for (_,_,o),(_,_,n) in zip(entries,shared))
(out/'font-fitting-comparison.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))
