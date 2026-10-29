"""Audit source ownership/grouping over PDFs without network calls or rendering."""
import argparse
from collections import Counter
import json
from pathlib import Path
from app.agents.text_reader import TextReaderAgent
from app.agents.block_maker import BlockMakerAgent
from app.services.rich_text import group_blocks


def audit(paths):
    records=[]
    for path in paths:
        try:
            pages=TextReaderAgent().run(path)
            raw=BlockMakerAgent().run('audit',pages)
            grouped=group_blocks(raw,pages,min_spans=4)
            ids=[s['id'] for b in grouped for s in b['style'].get('rich_text',{}).get('source_spans',[{'id':b['id']}])]
            records.append({'file':path.name,'pages':len(pages),'source_spans':len(raw),
                'regions':len(grouped),'coverage_ok':Counter(ids)==Counter(b['id'] for b in raw) and len(ids)==len(set(ids)),
                'cross_block_paragraphs':sum(b['style'].get('layout_policy',{}).get('source_block_count',1)>1 for b in grouped)})
        except Exception as exc:
            records.append({'file':path.name,'coverage_ok':False,'error_type':type(exc).__name__})
    return {'documents':len(records),'failures':sum(not r['coverage_ok'] for r in records),
            'network_calls':0,'records':records}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    paths=sorted(args.sources.glob('*.pdf'))
    if not paths:parser.error('No PDFs found in --sources')
    if args.output.exists():parser.error('Output already exists; choose a new report path')
    result=audit(paths)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2))
    print(json.dumps({key:value for key,value in result.items() if key!='records'}))
    return int(bool(result['failures']))


if __name__=='__main__':raise SystemExit(main())
