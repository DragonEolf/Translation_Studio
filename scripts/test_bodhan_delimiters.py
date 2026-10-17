"""Live, synthetic-only delimiter experiment. Never imports document/job data."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
import time
import unicodedata
import httpx
from app.config import Settings

TEXTS = [
    'The meeting has been postponed until Friday.',
    'Keep invoice INV-2048 unchanged; the amount is 1,250.50 INR.',
    'Visit https://example.org/report?q=7&lang=en and email help@example.org.',
    'Use A | B, <draft>, [notes], {items}, "quotes", and a backslash \\. Finish the review.',
    'Save the report.\nDo not delete the original.',
]
FORMATS = ['xml', 'brackets', 'unicode', 'markdown', 'inline_code', 'latex_labels', 'invisible', 'fenced_control']
EXTENDED = [
    'Symbols: § ¶ † ‡ © ® ™ • — – … Ω α β × ÷ ± ≠ ≤ ≥ ∞ ° # @ $ % ^ & * _ + = / : ; ? !. Keep the report safe.',
    'The discount is 12.5%; temperature is -10°C, ratio 3:2, and formula x² + y² = z².',
    'The literal string <span id="TS999">example</span> is documentation, not a boundary.',
    'Literal markers: [[EXAMPLE]], ⟦EXAMPLE⟧, `EXAMPLE`, and \\label{EXAMPLE}. Translate the surrounding prose.',
    'Save this paragraph.\n\nLeave the original file unchanged.',
]

def encode(kind, texts):
    ids = [f'TS{i:03}' for i in range(1,len(texts)+1)]
    if kind == 'markdown':
        rows = ['| ID | Text |', '| --- | --- |']
        rows += [f'| `{i}` | '+t.replace('\\','\\\\').replace('|',r'\|').replace('\n','<br/>')+' |' for i,t in zip(ids,texts)]
        return '\n'.join(rows), [f'`{i}`' for i in ids]
    if kind == 'fenced_control':
        return '```text\n'+'\n'.join(f'{i}: {t}' for i,t in zip(ids,texts))+'\n```', ids
    if kind == 'xml':
        return '\n'.join(f'<span id="{i}">{html.escape(t,quote=False)}</span>' for i,t in zip(ids,texts)), [f'<span id="{i}">' for i in ids]+['</span>']*len(ids)
    markers = {
        'brackets': lambda i:(f'[[{i}]]',f'[[/{i}]]'),
        'unicode': lambda i:(f'⟦{i}⟧',f'⟦/{i}⟧'),
        'inline_code': lambda i:(f'`{i}`',f'`/{i}`'),
        'latex_labels': lambda i:(f'\\label{{{i}}}',f'\\label{{END{i}}}'),
        'invisible': lambda i:(f'\u2063{i}\u2063',f'\u2063/{i}\u2063'),
    }
    pairs=[markers[kind](i) for i in ids]
    return '\n\n'.join(a+'\n'+t+'\n'+b for (a,b),t in zip(pairs,texts)), [m for pair in pairs for m in pair]

def parse(kind, output, count):
    expected=[f'TS{i:03}' for i in range(1,count+1)]
    if kind=='fenced_control':return None
    if kind=='markdown':
        result=[]
        for line in output.splitlines():
            if re.search(r'TS\d{3}',line):
                cells=re.split(r'(?<!\\)\|',line.strip())
                if len(cells)!=4 or not re.fullmatch(r'\s*`(TS\d{3})`\s*',cells[1]):return None
                identity=re.fullmatch(r'\s*`(TS\d{3})`\s*',cells[1]).group(1)
                result.append((identity,cells[2].strip().replace('<br/>','\n').replace(r'\|','|').replace('\\\\','\\')))
        return result if [r[0] for r in result]==expected else None
    if kind=='xml':
        pattern=r'<span id="(TS\d{3})">(.*?)</span>'
        matches=list(re.finditer(pattern,output,re.S))
        result=[(m.group(1),html.unescape(m.group(2).strip())) for m in matches]
    else:
        patterns={
            'brackets':r'\[\[(TS\d{3})\]\](.*?)\[\[/\1\]\]',
            'unicode':r'⟦(TS\d{3})⟧(.*?)⟦/\1⟧',
            'inline_code':r'`(TS\d{3})`(.*?)`/\1`',
            'latex_labels':r'\\label\{(TS\d{3})\}(.*?)\\label\{END\1\}',
            'invisible':'\u2063(TS\\d{3})\u2063(.*?)\u2063/\\1\u2063',
        }
        matches=list(re.finditer(patterns[kind],output,re.S))
        result=[(m.group(1),m.group(2).strip()) for m in matches]
    if [r[0] for r in result]!=expected or any(not r[1] for r in result):return None
    position=0
    for m in matches:
        if output[position:m.start()].strip():return None
        position=m.end()
    if output[position:].strip():return None
    return result

def assess(kind, source, output, markers, texts):
    mapping=parse(kind,output,len(texts))
    counts=Counter(markers)
    # IDs also occur in closing markers; compare their occurrence counts too.
    exact=all(output.count(m)==source.count(m) for m in counts)
    extras=Counter(re.findall(r'TS\d{3}',output)) != Counter(re.findall(r'TS\d{3}',source))
    checks=[]
    if mapping:
        for index,(identity,text) in enumerate(mapping):
            normalized=''.join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in text)
            anchors=[x for x in ['INV-2048','1,250.50','https://example.org/report?q=7&lang=en','help@example.org'] if x in texts[index]]
            checks.append({'id':identity,'text':text,'source_unchanged':text==texts[index], 'anchors_ok':all(a in normalized for a in anchors)})
    return {'markers_exact':exact,'unexpected_id_counts':extras,'mapping_valid':mapping is not None,'segments':checks,'all_content_changed':bool(checks) and all(not s['source_unchanged'] for s in checks),'anchors_ok':bool(checks) and all(s['anchors_ok'] for s in checks),'negative_control_source_retained':kind=='fenced_control' and all(t in output for t in texts)}

async def main(args):
    key=Settings().bodhan_api_key
    if not key:raise RuntimeError('Translation key is not configured')
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    base = TEXTS + EXTENDED if args.extended else TEXTS
    texts = [base[i % len(base)] for i in range(args.spans)]
    records=[];next_request=0
    async with httpx.AsyncClient(timeout=90) as client:
        for language in args.languages:
            for kind in args.formats:
                source,markers=encode(kind,texts)
                record={'format':kind,'language':language,'source':source,'attempts':[]}
                for attempt in range(2):
                    await asyncio.sleep(max(0,next_request-time.monotonic()))
                    start=time.perf_counter()
                    try:
                        response=await client.post('https://api.bodhan.ai/v1/chat/completions',headers={'Authorization':f'Bearer {key}'},json={'model':'indic-translate','messages':[{'role':'user','content':source}],'target_language_code':language,'target_script':'native'})
                    except httpx.TransportError as exc:
                        record['attempts'].append({'error':type(exc).__name__,'seconds':round(time.perf_counter()-start,3)})
                        record['error']='transport_error'
                        break
                    elapsed=round(time.perf_counter()-start,3)
                    record['attempts'].append({'status':response.status_code,'seconds':elapsed,'request_id':response.headers.get('x-request-id')})
                    try: allowance=float(response.headers.get('x-ratelimit-limit-requests','0'))
                    except ValueError:allowance=0
                    next_request=time.monotonic()+(60/allowance+.1 if allowance>0 else 1)
                    if response.status_code==429 and attempt==0:
                        try:delay=float(response.headers.get('retry-after','5'))
                        except ValueError:delay=5
                        next_request=max(next_request,time.monotonic()+min(delay,60))
                        continue
                    if not response.is_success:
                        record['error']=f'http_{response.status_code}'
                        break
                    body=response.json()
                    try:
                        choice=body['choices'][0];output=choice['message']['content']
                    except (KeyError,IndexError,TypeError):
                        record['error']='invalid_response';break
                    record.update(output=output,usage=body.get('usage',{}),finish_reason=choice.get('finish_reason'),truncated=bool(body.get('truncated')) or choice.get('finish_reason')!='stop')
                    record.update(assess(kind,source,output,markers,texts))
                    break
                records.append(record)
                (out/'results.json').write_text(json.dumps({'created_at':datetime.now(timezone.utc).isoformat(),'texts':texts,'records':records},ensure_ascii=False,indent=2))
                print(language,kind,'status='+str(record.get('error','ok')),'exact='+str(record.get('markers_exact')),'mapping='+str(record.get('mapping_valid')),'translated='+str(record.get('all_content_changed')),'anchors='+str(record.get('anchors_ok')),flush=True)
                if record.get('error') in {'http_401','http_403','http_429'}:
                    print('Stopping after authentication or rate-limit failure.',flush=True);return

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--languages',nargs='+',default=['hi','ta'])
    p.add_argument('--formats',nargs='+',choices=FORMATS,default=FORMATS)
    p.add_argument('--output',default='artifacts/bodhan-delimiter-test')
    p.add_argument('--spans',type=int,default=5)
    p.add_argument('--extended',action='store_true')
    asyncio.run(main(p.parse_args()))
