"""Harness checks use synthetic PDFs; the downloaded 40-page suite is opt-in."""
from collections import Counter
import json

import pymupdf as fitz
import pytest

from benchmarks.doclaynet.prepare import select_pages
from benchmarks.doclaynet.run import replacement, render_case, NoNetwork


def test_sample_is_reproducible_balanced_and_has_distinct_documents():
    images = [{'id': i, 'file_name': f'{i}.png', 'doc_name': f'doc-{i//2}',
               'doc_category': ['manuals', 'patents'][i % 2], 'precedence': 0} for i in range(100)]
    images.append({**images[0], 'id': 101, 'precedence': 1})
    coco = {'images': images, 'categories': [{'id': 1, 'name': 'Text'}],
            'annotations': [{'image_id': i, 'category_id': 1} for i in range(100)]}
    pages = select_pages(coco)
    assert pages == select_pages(coco)
    assert len(pages) == len({p['doc_name'] for p in pages}) == 40
    assert Counter(p['doc_category'] for p in pages) == {'manuals': 20, 'patents': 20}
    assert all(p['precedence'] == 0 for p in pages)


def test_replacements_preserve_literals_and_have_no_randomness():
    text = 'Hello world INV-2048 costs 1250.50 at https://example.com/path.'
    for profile in ['identity', 'expand150', 'hindi', 'tamil', 'urdu']:
        result = replacement(text, profile)
        assert result == replacement(text, profile)
        for literal in ['INV-2048', '1250.50', 'https://example.com/path.']:
            assert literal in result
    assert replacement(text, 'identity') == text
    assert len(replacement(text, 'expand150')) > len(text)


def fixture_pdf(path):
    with fitz.open() as doc:
        page = doc.new_page(width=500, height=250)
        x = 30
        for text, font in [('Hello ', 'helv'), ('world ', 'hebo'), ('from ', 'helv'), ('our team.', 'heit')]:
            page.insert_text((x, 60), text, fontname=font, fontsize=12)
            x += fitz.get_text_length(text, fontname=font, fontsize=12)
        page.draw_rect((30, 130, 180, 170), color=(1, 0, 0))
        doc.save(path)


@pytest.mark.parametrize('profile', ['identity', 'expand150'])
def test_real_renderer_uses_identical_source_replacements_across_modes(tmp_path, profile):
    source = tmp_path/'source.pdf'
    fixture_pdf(source)
    results = [render_case(source, tmp_path/mode, mode, profile, None)
               for mode in ['spans', 'paragraphs', 'auto']]
    for result in results:
        assert result['source_spans'] == result['rendered_source_spans'] == 4
        assert result['artifact'] == 'final.pdf'
        assert result['hard_issue_count'] == 0
    normalize = lambda s: ''.join(s.split())
    texts = [normalize(''.join(r['replacement'] for r in result['layouts'])) for result in results]
    assert len(set(texts)) == 1
    for mode in ['spans', 'paragraphs', 'auto']:
        assert (tmp_path/mode/'preview.jpg').exists()
        assert json.loads((tmp_path/mode/'result.json').read_text())['source_spans'] == 4
        with fitz.open(tmp_path/mode/'final.pdf') as pdf:
            spans = [span for block in pdf[0].get_text('dict')['blocks'] if block['type'] == 0
                     for line in block['lines'] for span in line['spans']]
            assert any(span['flags'] & 16 for span in spans), 'Bold style was lost'
            assert any(span['flags'] & 2 for span in spans), 'Italic style was lost'
            assert len(pdf[0].get_drawings()) == 1, 'Non-text rectangle changed'


def test_empty_or_image_only_page_cannot_pass(tmp_path):
    source = tmp_path/'source.pdf'
    with fitz.open() as doc:
        doc.new_page()
        doc.save(source)
    with pytest.raises(ValueError, match='selectable-text'):
        render_case(source, tmp_path/'empty', 'auto', 'identity', None)


@pytest.mark.asyncio
async def test_model_guards():
    with pytest.raises(AssertionError, match='forbidden'):
        await NoNetwork().translate('text')
    with pytest.raises(AssertionError, match='forbidden'):
        await NoNetwork().extract(b'image')


def test_comparison_catches_regressions_and_rejects_incomplete_runs():
    from copy import deepcopy
    from benchmarks.doclaynet.compare import compare
    baseline = {'provenance': {'manifest_sha256': 'sample', 'page_count': 1,
                              'modes': ['auto'], 'profiles': ['identity'], 'font_sha256': {}},
                'cases': [{'page': '01', 'mode': 'auto', 'profile': 'identity', 'status': 'review',
                           'source_spans': 12, 'retained_source_spans': 1, 'hard_issue_count': 2,
                           'small_fitted_blocks': 3, 'issue_counts': {'collision': 2}}]}
    assert compare(baseline, deepcopy(baseline)) == []
    current = deepcopy(baseline)
    current['cases'][0]['retained_source_spans'] = 2
    current['cases'][0]['issue_counts'] = {'missing_text': 2}
    metrics = {r['metric'] for r in compare(baseline, current)}
    assert metrics == {'retained_source_spans', 'missing_text'}
    current['cases'] = []
    with pytest.raises(ValueError, match='complete runs'):
        compare(baseline, current)


def test_range_reader_fetches_zip_members_without_full_archive():
    import io
    import zipfile
    import httpx
    from benchmarks.doclaynet.prepare import RangeFile
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('PDF/sample.pdf', b'%PDF-fixture')
    data = payload.getvalue()
    requests = []

    def respond(request):
        value = request.headers['Range']
        requests.append(value)
        first, last = value.removeprefix('bytes=').split('-')
        first, last = int(first), int(last)
        return httpx.Response(206, headers={'Content-Range': f'bytes {first}-{last}/{len(data)}'},
                              content=data[first:last+1])

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with zipfile.ZipFile(RangeFile(client, 'https://dataset.example/archive.zip')) as archive:
            assert archive.read('PDF/sample.pdf') == b'%PDF-fixture'
    assert requests[0] == 'bytes=0-0'
    assert len(requests) > 1
