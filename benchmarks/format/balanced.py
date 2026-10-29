"""Build a source-balanced ForMaT collection, reusing cached PDFs."""
import collections, hashlib, json, random, zipfile, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pymupdf
from benchmarks.format.prepare import ROOT

def fetch(item):
    try:
        req=urllib.request.Request(item['url'],headers={'User-Agent':'Mozilla/5.0'})
        with urllib.request.urlopen(req,timeout=30) as response:
            if not response.geturl().startswith('https://'): raise ValueError('Non-HTTPS redirect')
            content=response.read(40_000_001)
        if len(content)>40_000_000: raise ValueError('File exceeds 40 MB limit')
        return item,content,None
    except Exception as e: return item,None,str(e)[:200]

# Limited suppliers: Lenovo 4, Huawei 3 and Philips 7 in the catalog.
QUOTAS={'united_nations':36,'nissan_techinfo':25,'toyota':25,'lenovo':4,'huawei':3,'philips':7}

def main():
    old=json.loads((ROOT/'manifest.json').read_text()); existing={x['url']:x for x in old['documents']}; selected=[]; failures=[]
    if (ROOT/'balanced-manifest.json').exists():
        existing.update({x['url']:x for x in json.loads((ROOT/'balanced-manifest.json').read_text())['documents']})
    catalog={}
    with zipfile.ZipFile(ROOT/'ForMaT_Dataset.zip') as z:
        for name in z.namelist():
            if '__MACOSX' in name or '/en-' not in name or not name.endswith('.json'): continue
            for group in json.loads(z.read(name)):
                for r in group.get('records',[]):
                    if r.get('language')!='en' or not r.get('url','').startswith('https://'): continue
                    if group.get('source') not in QUOTAS or not 0 < r.get('size',0) <= 40000000: continue
                    catalog.setdefault(r['url'],{'id':r['id'],'url':r['url'],'title':r.get('title'),'source':group['source'],'page_count':r['page_count'],'catalog_size':r['size']})
    dest=ROOT/'pdfs'; dest.mkdir(exist_ok=True)
    seen_hash=set()
    for source,quota in QUOTAS.items():
        choices=[v for v in catalog.values() if v['source']==source]
        random.Random(20261029).shuffle(choices)
        # Prefer already verified downloads, then smaller PDFs.
        choices.sort(key=lambda v: (v['url'] not in existing,v['catalog_size']))
        found=[]
        for item in choices:
            if item['url'] in existing:
                x=existing[item['url']]; p=ROOT/x['file']
                if p.exists() and hashlib.sha256(p.read_bytes()).hexdigest()==x['sha256'] and x['sha256'] not in seen_hash:
                    found.append(x); seen_hash.add(x['sha256'])
                    if len(found)==quota: break
        missing=quota-len(found)
        if missing:
            remaining=[v for v in choices if v['url'] not in existing]
            with ThreadPoolExecutor(max_workers=6) as pool:
                for item,content,error in pool.map(fetch,remaining[:max(missing*2, missing+6)]):
                    if len(found)>=quota: break
                    try:
                        if error: raise ValueError(error)
                        digest=hashlib.sha256(content).hexdigest()
                        if digest in seen_hash: raise ValueError('duplicate PDF')
                        with pymupdf.open(stream=content,filetype='pdf') as pdf:
                            if pdf.is_encrypted or not len(pdf) or not any(pdf[i].get_text().strip() for i in range(min(4,len(pdf)))): raise ValueError('encrypted or no native text in first four pages')
                            pages=len(pdf)
                        item.update(actual_pages=pages,sha256=digest,file=f'pdfs/{item["id"]}.pdf')
                        (ROOT/item['file']).write_bytes(content); found.append(item); seen_hash.add(digest)
                        print(f'{source}: {len(found)}/{quota}',flush=True)
                    except Exception as e: failures.append({'source':source,'url':item['url'],'reason':str(e)[:200]})
        selected.extend(found)
        print(f'{source}: selected {len(found)}/{quota}',flush=True)
        data={'seed':20261029,'description':'Source-balanced available English ForMaT PDFs; not a random or representative sample. Original full PDFs, no page truncation.', 'quotas':QUOTAS,'documents':selected,'failures':failures}
        (ROOT/'balanced-manifest.json').write_text(json.dumps(data,indent=2))
    print(f'Total {len(selected)}',flush=True)

if __name__=='__main__': main()
