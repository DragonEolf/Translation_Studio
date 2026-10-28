from pathlib import Path
from types import SimpleNamespace as N

import pymupdf as fitz
import pytest

from app.agents.block_maker import BlockMakerAgent
from app.agents.text_reader import TextReaderAgent
from app.services.renderer import Renderer
from app.services.rich_text import group_blocks, encode_groups, validate_groups, format_hash
from app.services.underlines import replaced_underlines


def source(path):
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=240)
        page.insert_htmlbox(fitz.Rect(20, 30, 380, 90),
                            'Plain <span style="text-decoration:underline">underlined words</span> plain',
                            css='body {font-size:14pt}')
        # A structural rule must survive removal of a text decoration.
        page.draw_line((20, 180), (380, 180), width=.6)
        doc.save(path)
    pages = TextReaderAgent().run(path)
    return pages, BlockMakerAgent().run('test', pages)


def test_underline_is_an_inline_style_and_affects_revision_hash(tmp_path):
    pages, spans = source(tmp_path / 'source.pdf')
    assert any(b['style']['underline'] for b in spans)
    assert any(not b['style']['underline'] for b in spans)
    block = N(**group_blocks(spans, pages)[0])
    ids, payload = encode_groups([block], 'Utest')
    _, runs = validate_groups(payload, ids, [block])[0]
    assert ''.join(r['text'] for r in runs if r['style'].get('underline')).strip() == 'underlined words'
    original = format_hash(block)
    for style in block.style['rich_text']['styles'].values():
        style['underline'] = False
    assert original != format_hash(block)


@pytest.mark.parametrize('paragraph', [False, True])
def test_underline_moves_and_resizes_with_rendered_text(tmp_path, paragraph):
    path = tmp_path / 'source.pdf'
    pages, spans = source(path)
    underlined = next(b for b in spans if b['style']['underline'])
    if paragraph:
        block = N(**group_blocks(spans, pages)[0])
        runs = [{'text': 'short', 'style': underlined['style']}]
        translation = N(text='short', target_language='en', script='native',
                        metadata_json={'rich_text': {'runs': runs}})
    else:
        block = N(**underlined)
        translation = N(text='short', target_language='en', script='native', metadata_json={})
    layout = N(bbox=[20, 100, 180, 135], font='sans-serif', font_size=14, fit_status='fitted')
    destination = tmp_path / 'translated.pdf'
    Renderer().render_page(path, 1, [(block, translation, layout)], destination)
    with fitz.open(destination) as doc:
        drawings = doc[0].get_drawings()
        assert any(abs(d['rect'].y0 - 180) < .1 for d in drawings)
        assert not any(30 < d['rect'].y0 < 90 for d in drawings)
        new_lines = [d['rect'] for d in drawings if 100 < d['rect'].y0 < 135]
        assert new_lines and all(0 < r.width < 60 for r in new_lines)
        assert 'short' in doc[0].get_text()


def test_failed_owner_retains_a_shared_underline(tmp_path):
    path = tmp_path / 'shared.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_htmlbox(fitz.Rect(20, 30, 380, 90),
                            '<u>First <b>second</b></u>')
        doc.save(path)
    spans = BlockMakerAgent().run('test', TextReaderAgent().run(path))
    owners = [N(**b) for b in spans if b['style']['underline_paths']]
    assert len(owners) == 2
    assert not replaced_underlines([(owners[0], None, N(fit_status='fitted')),
                                    (owners[1], None, N(fit_status='overflow'))])
    assert len(replaced_underlines([(b, None, N(fit_status='fitted')) for b in owners])) == 1


def test_filled_underline_removed_without_erasing_table_border(tmp_path):
    path = tmp_path / 'filled.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=240)
        page.insert_text((30, 60), 'Heading', fontsize=14)
        width = fitz.get_text_length('Heading', fontsize=14)
        page.draw_rect(fitz.Rect(30, 62, 30 + width, 63), color=None, fill=(0, 0, 0))
        page.draw_rect(fitz.Rect(25, 20, 200, 80), width=.5)
        doc.save(path)
    block = N(**BlockMakerAgent().run('test', TextReaderAgent().run(path))[0])
    assert block.style['underline'] and block.style['underline_paths']
    layout = N(bbox=[30, 100, 180, 130], font='sans-serif', font_size=14, fit_status='fitted')
    translation = N(text='Title', target_language='en', script='native', metadata_json={})
    dest = tmp_path / 'output.pdf'
    Renderer().render_page(path, 1, [(block, translation, layout)], dest)
    with fitz.open(dest) as doc:
        drawings = doc[0].get_drawings()
        assert any(d['rect'] == fitz.Rect(25, 20, 200, 80) for d in drawings)
        assert not any(d['type'] == 'f' and abs(d['rect'].y0-62) < .1 for d in drawings)


def test_indic_underline_wraps_with_shaped_text(tmp_path):
    from app.config import Settings
    from app.services.text_fitter import FontRegistry
    fonts = FontRegistry(Settings().studio_font_dir or Path('fonts'))
    font = fonts.resolve('mr', 'native')
    if not font or font == 'missing':
        pytest.skip('Devanagari font unavailable')
    path = tmp_path / 'source.pdf'
    _, spans = source(path)
    block = N(**next(b for b in spans if b['style']['underline']))
    text = 'कर्करोग संशोधन आणि माहिती विश्लेषण'
    translation = N(text=text, target_language='mr', script='native', metadata_json={})
    layout = N(bbox=[20, 100, 160, 170], font=font, font_size=14, fit_status='fitted')
    destination = tmp_path / 'marathi.pdf'
    Renderer(fonts).render_page(path, 1, [(block, translation, layout)], destination)
    with fitz.open(destination) as doc:
        assert text in doc[0].get_text()
        lines = [fitz.Rect(min(i[1].x, i[2].x), i[1].y, max(i[1].x, i[2].x), i[2].y)
                 for d in doc[0].get_drawings() if d['type'] == 's'
                 for i in d['items'] if i[0] == 'l' and abs(i[1].y-i[2].y) < .01
                 and 100 < i[1].y < 170]
        assert len({round(r.y0, 1) for r in lines}) >= 2
        assert all(r.x0 >= 20 and r.x1 <= 160 for r in lines)


def test_office_polygon_link_underline_is_removed_before_wrapping(tmp_path):
    path = tmp_path / 'office-link.pdf'
    text = 'contact.person@example.com'
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=260)
        page.insert_text((30, 60), text, fontsize=14, color=(0, 0, 1))
        right = 30 + fitz.get_text_length(text, fontsize=14)
        middle = (30 + right)/2
        shape = page.new_shape()
        shape.draw_polyline([(30, 62), (middle, 62), (right, 62),
                             (right, 63), (middle, 63), (30, 63), (30, 62)])
        shape.finish(color=None, fill=(0, 0, 1), even_odd=True)
        shape.commit()
        # A larger structural rectangle must not be treated as an underline.
        page.draw_rect(fitz.Rect(20, 20, 380, 230), width=.5)
        doc.save(path)
    block = N(**BlockMakerAgent().run('test', TextReaderAgent().run(path))[0])
    assert block.style['underline']
    assert len(block.style['underline_paths']) == 1
    translation = N(text='https://example.com/long/path', target_language='en', script='native', metadata_json={})
    layout = N(bbox=[30, 100, 150, 200], font='sans-serif', font_size=14, fit_status='fitted')
    dest = tmp_path / 'output.pdf'
    Renderer().render_page(path, 1, [(block, translation, layout)], dest)
    with fitz.open(dest) as doc:
        drawings = doc[0].get_drawings()
        assert not any(abs(d['rect'].y0-62) < .1 for d in drawings)
        assert any(d['rect'] == fitz.Rect(20, 20, 380, 230) for d in drawings)
        lines = [i for d in drawings for i in d['items'] if i[0] == 'l'
                 and abs(i[1].y-i[2].y) < .01 and 100 < i[1].y < 200]
        assert len({round(i[1].y, 1) for i in lines}) >= 2


def test_table_rules_do_not_become_text_underlines(tmp_path):
    path=tmp_path/'table.pdf'
    with fitz.open() as doc:
        page=doc.new_page(width=400,height=200)
        for y in (30,55,80):
            page.draw_line((20,y),(380,y),width=.7)
        for x in (20,200,380):
            page.draw_line((x,30),(x,80),width=.7)
        # The baseline is close enough to a rule to trigger MuPDF's heuristic.
        page.insert_text((45,53),'Ordinary cell',fontsize=12)
        page.insert_text((220,53),'Other cell',fontsize=12)
        page.insert_text((45,76),'Second row',fontsize=12)
        page.insert_htmlbox((220,56,370,78),'<u>Real underline</u>',css='div{font-size:10pt}')
        doc.save(path)
    blocks=BlockMakerAgent().run('table',TextReaderAgent().run(path))
    normal=[b for b in blocks if b['source_text'] in {'Ordinary cell','Other cell','Second row'}]
    assert len(normal)==3 and all(not b['style']['underline'] for b in normal)
    underlined=next(b for b in blocks if b['source_text']=='Real underline')
    assert underlined['style']['underline'] and underlined['style']['underline_paths']
