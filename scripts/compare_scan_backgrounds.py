"""Re-render saved OCR translations without API calls or database changes.

Usage: python -m scripts.compare_scan_backgrounds DOCUMENT_ID [--data-dir data]
"""
import argparse
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from copy import copy

import numpy as np
import pymupdf as fitz

import app.services.renderer as renderer_module
from app.services.renderer import Renderer
from app.agents.reorganiser import ReorganiserAgent
from app.agents.checker import normalized
from app.services.text_fitter import FontRegistry
from app.services.ocr_notation import display_text
from app.services.ocr_overlaps import overlap_reviews


class WhiteBackground:
    def __init__(self, page, regions):
        pass

    def paint(self, page, block):
        page.draw_rect(fitz.Rect(block.bbox), color=None, fill=(1, 1, 1), overlay=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('document_id')
    parser.add_argument('--data-dir', type=Path, default=Path('data'))
    parser.add_argument('--output-dir', type=Path, default=Path('output/pdf/scan-background-review'))
    parser.add_argument('--refit-ocr', action='store_true', help='Compare saved layouts with newly measured OCR layouts')
    parser.add_argument('--font-dir', type=Path, default=Path('fonts'))
    args = parser.parse_args()
    root = args.output_dir.resolve()
    work = root/'pages'
    work.mkdir(parents=True, exist_ok=True)
    source = args.data_dir/args.document_id/'source.pdf'
    connection = sqlite3.connect((args.data_dir/'studio.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute('select * from blocks where document_id=? order by page_number,reading_order', (args.document_id,)).fetchall()
    pages = {}
    for row in rows:
        block = SimpleNamespace(**dict(row))
        block.bbox, block.style = json.loads(block.bbox), json.loads(block.style)
        if not block.style.get('ocr'):
            continue
        translation = connection.execute('select * from translations where block_id=? order by revision desc limit 1', (block.id,)).fetchone()
        layout = connection.execute('select * from layouts where block_id=?', (block.id,)).fetchone()
        if translation is None or layout is None:
            continue
        translation = SimpleNamespace(**dict(translation))
        translation.metadata_json = json.loads(translation.metadata_json)
        layout = SimpleNamespace(**dict(layout))
        layout.bbox = json.loads(layout.bbox)
        pages.setdefault(block.page_number, []).append((block, translation, layout))
    connection.close()
    if not pages:
        raise ValueError('No saved OCR translations/layouts to compare')
    background_class = renderer_module.ScanBackground
    summary = []
    reorganiser = ReorganiserAgent(FontRegistry(args.font_dir.resolve())) if args.refit_ocr else None
    with fitz.open(source) as original, fitz.open() as improved, fitz.open() as comparison:
        for number, entries in sorted(pages.items()):
            current = work/f'local-{number}.pdf'
            previous = work/f'white-{number}.pdf'
            saved_entries = entries
            if args.refit_ocr:
                page_metadata = SimpleNamespace(width=original[number-1].rect.width, height=original[number-1].rect.height)
                entries = []
                reviews = overlap_reviews([b for b,_,_ in saved_entries])
                for block, translation, _ in saved_entries:
                    block = copy(block)
                    block.style = {**block.style, 'size':24.0}
                    block.style.pop('ocr_overlap_with', None)
                    if block.id in reviews:
                        block.style['ocr_overlap_with'] = reviews[block.id]
                    obstacles = [b.bbox for b,_,_ in saved_entries if b.id != block.id]
                    layout = SimpleNamespace(**reorganiser.run(block, translation, page_metadata, obstacles))
                    entries.append((block, translation, layout))
                Renderer().render_page(source, number, saved_entries, previous)
            else:
                try:
                    renderer_module.ScanBackground = WhiteBackground
                    Renderer().render_page(source, number, saved_entries, previous)
                finally:
                    renderer_module.ScanBackground = background_class
            Renderer().render_page(source, number, entries, current)
            with fitz.open(current) as after, fitz.open(previous) as before:
                improved.insert_pdf(after)
                page = comparison.new_page(width=1230, height=620)
                page.insert_text((15, 20), f'Source page {number} | Same saved OCR and translation; no model calls', fontsize=11)
                before_label = 'Previous: saved fitting' if args.refit_ocr else 'Previous: white fill'
                after_label = 'New: measured OCR fitting' if args.refit_ocr else 'New: local paper and lighting'
                for i, (label, document, index) in enumerate([('Original scan', original, number-1), (before_label, before, 0), (after_label, after, 0)]):
                    page.insert_text((15+i*410, 42), label, fontsize=12)
                    page.show_pdf_page(fitz.Rect(10+i*410, 52, 410+i*410, 615), document, index)
                page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(work/f'comparison-{number}.png')
                if not args.refit_ocr:
                    assert after[0].get_text() == before[0].get_text(), 'Background changed semantic text'
                for block, translation, layout in entries:
                    if layout.fit_status == 'fitted':
                        extracted = after[0].get_textbox(fitz.Rect(layout.bbox)+(-1,-1,1,1))
                        assert normalized(display_text(block, translation.text)) in normalized(extracted), f'Translated text missing: {block.id}'
                old_pix, new_pix = before[0].get_pixmap(), after[0].get_pixmap()
                old_pixels = np.frombuffer(old_pix.samples, np.uint8).reshape(old_pix.height, old_pix.width, old_pix.n)
                new_pixels = np.frombuffer(new_pix.samples, np.uint8).reshape(new_pix.height, new_pix.width, new_pix.n)
                allowed = np.zeros((new_pix.height, new_pix.width), bool)
                for block, _, layout in entries+saved_entries:
                    if layout.fit_status == 'fitted':
                        x0, y0, x1, y1 = block.bbox
                        allowed[max(0,int(y0)-2):min(new_pix.height,int(y1)+3), max(0,int(x0)-2):min(new_pix.width,int(x1)+3)] = True
                outside_changes = int(np.count_nonzero(np.any(old_pixels != new_pixels, axis=2) & ~allowed))
                assert outside_changes == 0, 'Background changed pixels outside replacement regions'
                summary.append({'source_page': number, 'fitted_regions_before':sum(l.fit_status == 'fitted' for _,_,l in saved_entries), 'fitted_regions': sum(l.fit_status == 'fitted' for _,_,l in entries), 'polygon_regions': sum(bool(b.style.get('source_polygon')) for b,_,_ in entries), 'all_fitted_translations_searchable': True, 'changed_pixels_outside_regions_with_2pt_margin': outside_changes, 'regions':[{'id':b.id,'status':l.fit_status,'font_size':l.font_size} for b,_,l in entries]})
        improved.save(root/('translated-ocr-pages.pdf' if args.refit_ocr else 'handwritten-tamil-local-background.pdf'), garbage=4, deflate=True)
        comparison.save(root/('fitting-comparison.pdf' if args.refit_ocr else 'background-comparison.pdf'), garbage=4, deflate=True)
    (root/'verification.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))
    print(root)


if __name__ == '__main__':
    main()
