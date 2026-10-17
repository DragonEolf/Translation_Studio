"""Install Regular/Bold Noto fonts for every supported target script."""
import hashlib
import json
from pathlib import Path
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import pymupdf as fitz
from app.services.text_fitter import FontRegistry, SCRIPT_FONTS
from app.providers.bodhan import LANGUAGES

root = Path(__file__).resolve().parents[1] / 'fonts'
base = 'https://raw.githubusercontent.com/notofonts/noto-fonts/main'
families = ['NotoSans'] + ['NotoSans' + script for script in sorted(set(SCRIPT_FONTS.values()))]
existing = FontRegistry(root)
jobs = []
for family in families:
    for weight in ['Regular', 'Bold']:
        filename = f'{family}-{weight}.ttf'
        found = next((p for p in root.rglob(filename) if p.name == filename), None)
        if found is None:
            jobs.append((family, filename))

def download(item):
    family, filename = item
    url = f'{base}/hinted/ttf/{family}/{filename}'
    with urllib.request.urlopen(url, timeout=45) as response:
        data = response.read()
    fitz.Font(fontbuffer=data)
    folder = root / 'supported' / family
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / filename
    temporary = destination.with_suffix('.tmp')
    temporary.write_bytes(data)
    temporary.replace(destination)
    return {'file':str(destination.relative_to(root)), 'bytes':len(data), 'sha256':hashlib.sha256(data).hexdigest(), 'url':url}

with ThreadPoolExecutor(max_workers=4) as pool:
    downloaded = list(pool.map(download, jobs))
(root/'supported').mkdir(exist_ok=True)
with urllib.request.urlopen(f'{base}/LICENSE',timeout=45) as response:
    (root/'supported'/'LICENSE').write_bytes(response.read())
registry = FontRegistry(root)
for language in sorted(LANGUAGES):
    for bold in [False,True]:
        path = registry.resolve(language,'native',bold)
        if path in {None,'missing'}:
            raise RuntimeError(f'Missing font: {language}, bold={bold}')
        fitz.Font(fontfile=path)
report = []
for family in families:
    files = sorted(root.rglob(f'{family}-*.ttf'))
    files = [f for f in files if f.name in {f'{family}-Regular.ttf',f'{family}-Bold.ttf'}]
    report.append({'family':family,'bytes':sum(p.stat().st_size for p in files),'files':[str(p.relative_to(root)) for p in files]})
(root/'supported'/'manifest.json').write_text(json.dumps({'source':base,'downloaded':downloaded,'families':report},indent=2))
print('Downloaded',len(downloaded),'files:',sum(x['bytes'] for x in downloaded),'bytes')
for item in report:print(item['family'],item['bytes'])
print('Verified Regular and Bold font resolution for',len(LANGUAGES),'target languages.')
