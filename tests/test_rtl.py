"""RTL is a text layout rule; source boxes and illustrations stay in place."""
from pathlib import Path
from types import SimpleNamespace as N
import pymupdf as fitz
import pytest
from app.agents.reorganiser import ReorganiserAgent
from app.services.layout_tools import layout_markup, fits
from app.services.renderer import Renderer
from app.services.text_fitter import FontRegistry, text_direction


@pytest.mark.parametrize('language,script,expected', [
    ('ur', 'native', 'rtl'), ('ks', 'native', 'rtl'),
    ('sd-Arab', 'native', 'rtl'), ('ur', 'roman', 'ltr'),
    ('ks-Deva', 'native', 'ltr'), ('sd', 'native', 'ltr'),
    ('hi', 'native', 'ltr'), ('en', 'native', 'ltr'),
])
def test_target_script_direction(language, script, expected):
    assert text_direction(language, script) == expected


@pytest.mark.parametrize('rich', [False, True])
@pytest.mark.parametrize('align', ['left', 'center'])
def test_fitting_and_rendering_use_same_rtl_markup(rich, align):
    fonts = FontRegistry(Path('fonts').resolve())
    text = 'یہ ایک مثال ہے 123 ABC'
    style = {'size': 16, 'color': 0, 'fixed_box': True, 'align': align}
    runs = [{'text': text, 'style': {'size': 16, 'bold': True}}]
    if rich:
        style['rich_text'] = {'version': 1}
    block = N(bbox=[30, 30, 350, 130], style=style)
    translation = N(text=text, target_language='ur', script='native',
                    metadata_json={'rich_text': {'runs': runs}} if rich else {})
    page = N(width=400, height=200)
    layout = N(**ReorganiserAgent(fonts).run(block, translation, page, []))
    assert layout.fit_status == 'fitted'
    assert layout.bbox == block.bbox
    assert fits(block, translation, layout, fonts, page)
    content, css, archive = layout_markup(block, translation, layout, fonts)
    assert 'dir="rtl"' in content and 'direction:rtl' in css
    assert f"text-align:{align}" in css
    # Actual glyph geometry confirms the renderer respects alignment.
    with fitz.open() as pdf:
        target = pdf.new_page(width=400, height=200)
        spare, _ = target.insert_htmlbox(layout.bbox, content, css=css, archive=archive, scale_low=1)
        assert spare >= 0
        words = target.get_text('words')
        assert words
        left, right = min(w[0] for w in words), max(w[2] for w in words)
        if align == 'left':
            assert right > 340 and left > 100
        else:
            assert abs((left + right) / 2 - 190) < 5
        chars = [c for b in target.get_text('rawdict')['blocks'] if 'lines' in b
                 for line in b['lines'] for span in line['spans'] for c in span['chars']]
        latin = [c for c in chars if c['c'] in 'ABC']
        assert [c['c'] for c in sorted(latin, key=lambda c:c['origin'][0])] == list('ABC')


def test_final_rtl_pdf_preserves_semantics_and_image_position(tmp_path):
    fonts = FontRegistry(Path('fonts').resolve())
    source = tmp_path / 'source.pdf'
    with fitz.open() as pdf:
        page = pdf.new_page(width=400, height=200)
        page.insert_text((30, 50), 'Source paragraph')
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
        pix.clear_with(100)
        page.insert_image(fitz.Rect(30, 140, 80, 190), pixmap=pix)
        pdf.save(source)
    text = 'یہ ایک مثال ہے 123 ABC'
    block = N(bbox=[30, 30, 350, 100], style={'size':16, 'color':0, 'fixed_box':True})
    translation = N(text=text, target_language='ur', script='native', metadata_json={})
    layout = N(**ReorganiserAgent(fonts).run(block, translation, N(width=400,height=200), []))
    destination = tmp_path / 'rtl.pdf'
    Renderer(fonts).render_page(source, 1, [(block, translation, layout)], destination)
    with fitz.open(destination) as pdf:
        assert text in pdf[0].get_text()
        assert list(pdf[0].get_image_info()[0]['bbox']) == [30, 140, 80, 190]
        assert 'Source paragraph' not in pdf[0].get_text()
