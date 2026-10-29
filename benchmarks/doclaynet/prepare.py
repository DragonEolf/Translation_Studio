"""Fetch a deterministic 40-page sample using HTTP ranges, not entire archives."""
import argparse
from collections import Counter, defaultdict
import hashlib
import io
import json
from pathlib import Path
import random
import time
import zipfile

import httpx

BASE = 'https://codait-cos-dax.s3.us.cloud-object-storage.appdomain.cloud/dax-doclaynet/1.0.0/'


class RangeFile(io.RawIOBase):
    def __init__(self, client, url):
        self.client, self.url, self.position = client, url, 0
        response = self._request('bytes=0-0')
        self.size = int(response.headers['content-range'].split('/')[-1])

    def _request(self, value):
        for attempt in range(4):
            try:
                # Distinct query prevents intermediate caches reusing another range.
                with self.client.stream('GET', self.url, params={'range': value}, headers={'Range': value}) as response:
                    response.raise_for_status()
                    if response.status_code != 206:
                        raise ValueError('Server must support HTTP ranges; refusing full archive download')
                    response.read()
                    return response
            except (httpx.HTTPError, ValueError):
                if attempt == 3:
                    raise
                time.sleep(attempt + 1)

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        self.position = offset if whence == 0 else (self.position if whence == 1 else self.size) + offset
        return self.position

    def read(self, size=-1):
        end = self.size - 1 if size < 0 else min(self.size - 1, self.position + size - 1)
        if end < self.position:
            return b''
        response = self._request(f'bytes={self.position}-{end}')
        expected = f'bytes {self.position}-{end}/{self.size}'
        if response.headers.get('content-range') != expected or len(response.content) != end-self.position+1:
            raise ValueError('Incorrect byte range returned')
        self.position = end + 1
        return response.content


def select_pages(coco, count=40, seed=20261028):
    """Balance document categories, then favor underrepresented layout labels.

    Only official test pages, one page per source document; exclude duplicate
    annotation variants (precedence > 0). Never select on renderer performance.
    """
    labels = {c['id']: c['name'] for c in coco['categories']}
    features = defaultdict(Counter)
    for annotation in coco['annotations']:
        features[annotation['image_id']][labels[annotation['category_id']]] += 1
    groups = defaultdict(list)
    for image in coco['images']:
        if image.get('precedence', 0) == 0:
            groups[image.get('doc_category', 'unknown')].append(image)
    rng = random.Random(seed)
    for rows in groups.values():
        rows.sort(key=lambda x: x['file_name'])
        rng.shuffle(rows)
    chosen, documents, seen = [], set(), Counter()
    categories = sorted(groups)
    while len(chosen) < count:
        progressed = False
        for category in categories:
            candidates = [r for r in groups[category] if r.get('doc_name', r['file_name']) not in documents]
            if not candidates:
                continue
            best = max(candidates, key=lambda r: sum(1/(1+seen[f]) for f in features[r['id']]))
            chosen.append({**best, 'layout_counts': dict(features[best['id']])})
            documents.add(best.get('doc_name', best['file_name']))
            seen.update(features[best['id']].keys())
            progressed = True
            if len(chosen) == count:
                break
        if not progressed:
            raise ValueError(f'Only {len(chosen)} distinct documents available')
    return chosen


def prepare(root, count=40, seed=20261028):
    root.mkdir(parents=True, exist_ok=True)
    annotation_path = root / 'test-annotations.json'
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        pinned_path = Path(__file__).with_name('manifest.json')
        pinned = json.loads(pinned_path.read_text()) if count == 40 and seed == 20261028 and pinned_path.exists() else None
        if not pinned and not annotation_path.exists():
            print('Fetching official test annotations via byte ranges', flush=True)
            with zipfile.ZipFile(RangeFile(client, BASE+'DocLayNet_core.zip')) as archive:
                name = next(n for n in archive.namelist() if n.endswith('COCO/test.json'))
                annotation_path.write_bytes(archive.read(name))
        pages = pinned['pages'] if pinned else select_pages(json.loads(annotation_path.read_text()), count, seed)
        with zipfile.ZipFile(RangeFile(client, BASE+'DocLayNet_extra.zip')) as archive:
            names = {Path(n).name: n for n in archive.namelist() if n.endswith('.pdf')}
            for index, page in enumerate(pages, 1):
                filename = Path(page['file_name']).stem + '.pdf'
                path = root / 'pdfs' / filename
                path.parent.mkdir(exist_ok=True)
                if not path.exists():
                    payload = archive.read(names[filename])  # ZIP CRC verified here.
                    path.write_bytes(payload)
                page['pdf'] = 'pdfs/' + filename
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if page.get('sha256') and page['sha256'] != digest:
                    raise ValueError(f'Checksum mismatch: {path}')
                page['sha256'] = digest
                print(f'{index}/{count}: {page.get("doc_category")} {filename}', flush=True)
        manifest = {'dataset': 'DocLayNet 1.0.0', 'split': 'test', 'seed': seed,
                    'source': BASE, 'annotation_sha256': pinned['annotation_sha256'] if pinned else hashlib.sha256(annotation_path.read_bytes()).hexdigest(),
                    'selection': 'Round-robin document categories; rare layout-label coverage; distinct source documents; precedence=0',
                    'pages': pages}
        (root/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
        print(json.dumps(dict(Counter(p.get('doc_category') for p in pages)), indent=2))
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('data/benchmarks/doclaynet40'))
    parser.add_argument('--count', type=int, default=40)
    parser.add_argument('--seed', type=int, default=20261028)
    args = parser.parse_args()
    if args.count < 1:
        parser.error('--count must be positive')
    prepare(args.root, args.count, args.seed)
