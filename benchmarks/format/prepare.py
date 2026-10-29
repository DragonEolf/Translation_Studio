"""Download and validate short English ForMaT source documents for local tests."""
import hashlib, json, random, urllib.request, zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pymupdf

ROOT = Path('data/benchmarks/format')

def fetch(item):
    try:
        req=urllib.request.Request(item['url'],headers={'User-Agent':'Mozilla/5.0'})
        with urllib.request.urlopen(req,timeout=12) as response:
            if not response.geturl().startswith('https://'): raise ValueError('Non-HTTPS redirect')
            content=response.read(15_000_001)
        if len(content)>15_000_000: raise ValueError('File exceeds size limit')
        return item, content, None
    except Exception as e:
        return item, None, str(e)[:200]

def main():
    items = {}
    with zipfile.ZipFile(ROOT/'ForMaT_Dataset.zip') as archive:
        for name in archive.namelist():
            if '__MACOSX' in name or not name.endswith('.json') or '/en-' not in name:
                continue
            for group in json.loads(archive.read(name)):
                for record in group.get('records', []):
                    if record.get('language') == 'en' and 1 <= record.get('page_count', 9999) <= 100 and record.get('size', 99999999) <= 15000000 and record.get('url', '').startswith('https://'):
                        items.setdefault(record['url'], {'id':record['id'], 'url':record['url'], 'title':record.get('title'), 'source':group.get('source'), 'page_count':record['page_count']})
    candidates = list(items.values()); random.Random(20261029).shuffle(candidates)
    out = ROOT/'pdfs'; out.mkdir(parents=True, exist_ok=True)
    manifest_path = ROOT/'manifest.json'; manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {'seed':20261029, 'documents':[], 'failures':[]}
    done = {x['url'] for x in manifest['documents']} | {x['url'] for x in manifest['failures']}
    print(f'{len(candidates)} candidates; {len(manifest["documents"])} already downloaded', flush=True)
    candidates = [item for item in candidates if item['url'] not in done]
    with ThreadPoolExecutor(max_workers=12) as pool:
      for item, content, error in pool.map(fetch, candidates):
        if len(manifest['documents']) >= 100: break
        try:
            if error: raise ValueError(error)
            with pymupdf.open(stream=content,filetype='pdf') as doc:
                if doc.is_encrypted or not 1 <= len(doc) <= 100: raise ValueError('Encrypted or unexpected page count')
                if not any(p.get_text().strip() for p in doc): raise ValueError('No native text')
                item['actual_pages']=len(doc)
            item['sha256']=hashlib.sha256(content).hexdigest(); item['file']=f'pdfs/{item["id"]}.pdf'
            (ROOT/item['file']).write_bytes(content); manifest['documents'].append(item)
            print(f'Downloaded {len(manifest["documents"])}: {item["source"]} ({item["actual_pages"]} pages)',flush=True)
        except Exception as e:
            manifest['failures'].append({'url':item['url'],'reason':str(e)[:200]})
        manifest_path.write_text(json.dumps(manifest,indent=2))
    print(f'Finished: {len(manifest["documents"])} documents, {len(manifest["failures"])} unavailable',flush=True)

if __name__ == '__main__': main()
