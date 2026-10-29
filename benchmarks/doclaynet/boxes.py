"""Compare native PDF text spans against DocLayNet test annotations (offline)."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import pymupdf as fitz
from app.agents.text_reader import TextReaderAgent
from app.agents.block_maker import BlockMakerAgent
from app.services.rich_text import group_blocks, AUTO_PARAGRAPH_MIN_SPANS

TEXT = {'Caption', 'Footnote', 'List-item', 'Page-footer', 'Page-header', 'Section-header', 'Text', 'Title'}

def intersection(a, b):
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None

def area(r):
    return max(0, r[2]-r[0])*max(0, r[3]-r[1])

def union_area(rects):
    xs = sorted({v for r in rects for v in (r[0], r[2])})
    total = 0.0
    for x0, x1 in zip(xs, xs[1:]):
        intervals = sorted((r[1], r[3]) for r in rects if r[0] < x1 and r[2] > x0)
        if not intervals: continue
        start, end = intervals[0]
        for lo, hi in intervals[1:]:
            if lo > end:
                total += (x1-x0)*(end-start); start, end = lo, hi
            else: end = max(end, hi)
        total += (x1-x0)*(end-start)
    return total

def score(refs, preds, threshold):
    pairs = []
    for i, ref in enumerate(refs):
        for j, pred in enumerate(preds):
            overlap = intersection(ref['bbox'], pred['bbox'])
            if overlap:
                a = area(overlap); iou = a/(area(ref['bbox'])+area(pred['bbox'])-a)
                if iou >= threshold: pairs.append((iou, i, j))
    used_r, used_p = set(), set()
    for _, i, j in sorted(pairs, reverse=True):
        if i not in used_r and j not in used_p: used_r.add(i); used_p.add(j)
    tp = len(used_r); p = tp/len(preds) if preds else 0; r = tp/len(refs) if refs else 0
    return {'matched': tp, 'reference': len(refs), 'predicted': len(preds), 'precision': p, 'recall': r, 'f1': 2*p*r/(p+r) if p+r else 0}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', type=Path, default=Path('data/benchmarks/doclaynet40'))
    ap.add_argument('--out', type=Path, default=Path('output/benchmarks/doclaynet-boxes'))
    ap.add_argument('--mode', choices=('spans', 'paragraphs', 'auto'), default='spans')
    args = ap.parse_args()
    manifest = json.loads((args.data/'manifest.json').read_text())
    raw = (args.data/'test-annotations.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest['annotation_sha256']: raise ValueError('Annotation checksum mismatch')
    coco = json.loads(raw); cats = {c['id']:c['name'] for c in coco['categories']}
    annotations = defaultdict(list)
    for a in coco['annotations']: annotations[a['image_id']].append(a)
    args.out.mkdir(parents=True, exist_ok=False)
    results = []; all_refs = []; all_preds = []; by_category = defaultdict(list)
    for page in manifest['pages']:
        source = args.data/page['pdf']
        if hashlib.sha256(source.read_bytes()).hexdigest() != page['sha256']: raise ValueError(f'PDF checksum mismatch: {source}')
        extracted = TextReaderAgent().run(source); blocks = BlockMakerAgent().run('bench', extracted)
        if args.mode != 'spans':
            blocks = group_blocks(blocks, extracted, min_spans=AUTO_PARAGRAPH_MIN_SPANS if args.mode == 'auto' else 1)
        w, h = extracted[0]['width'], extracted[0]['height']
        refs = []
        for a in annotations[page['id']]:
            name = cats[a['category_id']]
            if name not in TEXT: continue
            x,y,bw,bh = a['bbox']; refs.append({'bbox':[x,y,x+bw,y+bh], 'label':name})
        preds = [{'bbox':[b['bbox'][0]*page['width']/w,b['bbox'][1]*page['height']/h,b['bbox'][2]*page['width']/w,b['bbox'][3]*page['height']/h], 'label':b['type']} for b in blocks]
        covers = []
        for ref in refs:
            overlaps = [r for pred in preds if (r := intersection(ref['bbox'], pred['bbox']))]
            covers.append(union_area(overlaps)/area(ref['bbox']) if area(ref['bbox']) else 0)
        rec = {'id':page['id'],'pdf':page['pdf'],'doc_category':page['doc_category'], 'reference_count':len(refs), 'prediction_count':len(preds), 'scores':{str(t):score(refs,preds,t) for t in (.5,.75)}, 'mean_reference_coverage':sum(covers)/len(covers) if covers else None,'reference_coverage_at_50':sum(c>=.5 for c in covers), 'references':[{**r,'coverage':c} for r,c in zip(refs,covers)],'predictions':preds}
        results.append(rec); all_refs.extend(refs); all_preds.extend(preds)
        for ref,c in zip(refs,covers): by_category[ref['label']].append(c)
        with fitz.open(source) as doc:
            p = doc[0]
            for r in refs:
                x0,y0,x1,y1=r['bbox']; p.draw_rect(fitz.Rect(x0*w/page['width'],y0*h/page['height'],x1*w/page['width'],y1*h/page['height']),color=(0,0.65,0),width=.6,overlay=True)
            for b in blocks: p.draw_rect(fitz.Rect(b['bbox']),color=(1,0,0),width=.4,overlay=True)
            p.get_pixmap(matrix=fitz.Matrix(1,1),alpha=False).save(str(args.out/f"{page['id']}.png"))
    total_refs=sum(r['reference_count'] for r in results); total_preds=sum(r['prediction_count'] for r in results)
    aggregate={}
    for t in ('0.5','0.75'):
        tp=sum(r['scores'][t]['matched'] for r in results); precision=tp/total_preds; recall=tp/total_refs
        aggregate[t]={'matched':tp,'reference':total_refs,'predicted':total_preds,'precision':precision,'recall':recall,'f1':2*precision*recall/(precision+recall) if precision+recall else 0}
    summary={'mode': args.mode, 'description':'Native PDF boxes; class-agnostic strict greedy one-to-one IoU matching to text-only annotations; predictions include table/nontext-region boxes. Coverage is union of predicted rectangles inside each reference divided by reference area, not OCR completeness.', 'pages':len(results),'strict':aggregate,'reference_coverage':{k:{'count':len(v),'mean':sum(v)/len(v),'at_least_50_pct':sum(x>=.5 for x in v)} for k,v in sorted(by_category.items())}, 'results':results}
    (args.out/'summary.json').write_text(json.dumps(summary,indent=2)); print(json.dumps({k:v for k,v in summary.items() if k != 'results'},indent=2))

if __name__ == '__main__': main()
