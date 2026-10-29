from pathlib import Path
from app.services.underlines import replaced_underlines
import pymupdf as fitz
from app.services.layout_tools import layout_markup
from app.services.scan_background import ScanBackground
from app.services.ocr_notation import display_text


class Renderer:
    def __init__(self, fonts=None):
        self.fonts=fonts

    def render_page(self, source, number, entries, destination):
        with fitz.open(source) as original, fitz.open() as output:
            output.insert_pdf(original, from_page=number-1, to_page=number-1)
            page = output[0]
            fitted = [(b, t, layout) for b, t, layout in entries if layout.fit_status == "fitted"]
            background = ScanBackground(original[number-1], [b.bbox for b, _, _ in entries]) if any(b.style.get("ocr") for b, _, _ in fitted) else None
            # Remove only fully owned decoration paths; retain tables and rules.
            old_underlines = replaced_underlines(entries)
            for region in old_underlines:
                page.add_redact_annot(fitz.Rect(region), fill=False)
            if old_underlines:
                page.apply_redactions(images=0, graphics=1, text=1)
            for block, _, _ in fitted:
                if block.style.get("ocr"):
                    background.paint(page, block)
                else:
                    for region in block.style.get("source_regions",[block.bbox]):
                        page.add_redact_annot(fitz.Rect(region), fill=False)
            if fitted:
                page.apply_redactions(images=0, graphics=0, text=0)
            for block, translation, layout in fitted:
                visible_text = display_text(block, translation.text)
                font_path = None if layout.font == "sans-serif" else layout.font
                content, css, archive = layout_markup(block, translation, layout, self.fonts)
                if all(ord(c)<0x300 for c in visible_text):
                    spare,_=page.insert_htmlbox(fitz.Rect(layout.bbox),content,css=css,archive=archive,scale_low=1)
                    if spare<0:
                        raise RuntimeError("Validated layout failed while rendering")
                    continue
                with fitz.open() as shaped:
                    shape_page=shaped.new_page(width=page.rect.width,height=page.rect.height)
                    spare,_=shape_page.insert_htmlbox(fitz.Rect(layout.bbox),content,css=css,archive=archive,scale_low=1)
                    if spare<0:
                        raise RuntimeError("Validated layout failed while rendering")
                    # Store the correctly shaped visible glyphs as vector outlines.
                    # MuPDF's ligature ToUnicode mappings can lose or duplicate Indic
                    # text across lines; a separate semantic layer avoids that defect.
                    svg=shape_page.get_svg_image(text_as_path=True)
                    with fitz.open("svg",svg.encode("utf-8")) as vector:
                        with fitz.open("pdf",vector.convert_to_pdf()) as outlined:
                            page.show_pdf_page(page.rect,outlined,0)
                rect=fitz.Rect(layout.bbox)
                size=max(.5,rect.height/1.374)
                origin=fitz.Point(rect.x0,rect.y0+1.075*size)
                # Keep the semantic glyph inside the box despite PDF matrix
                # rounding, so exact-box extraction returns the whole ActualText.
                # MuPDF can retain the placeholder instead of ActualText when it
                # matches the replacement's suffix. Pick a different suffix.
                anchor = 'z' if visible_text.rstrip().endswith('x') else 'x'
                stretch=rect.width*.999/fitz.get_text_length(anchor,fontname="helv",fontsize=size)
                before=set(page.get_contents())
                page.insert_text(origin,anchor,fontsize=size,fontname="helv",render_mode=3,morph=(origin,fitz.Matrix(stretch,1)))
                actual="FEFF"+visible_text.encode("utf-16-be").hex().upper()
                for xref in set(page.get_contents())-before:
                    output.update_stream(xref,b"/Span << /ActualText <"+actual.encode("ascii")+b"> >> BDC\n"+output.xref_stream(xref)+b"\nEMC")
            output.save(destination)
            page.get_pixmap().save(str(destination.with_suffix(".png")))

    def assemble(self, source, page_paths, destination):
        with fitz.open(source) as original, fitz.open() as output:
            for index in range(len(original)):
                path = page_paths.get(index + 1)
                if path and path.exists():
                    with fitz.open(path) as page:
                        output.insert_pdf(page)
                else:
                    output.insert_pdf(original, from_page=index, to_page=index)
            temp = destination.with_suffix(".tmp.pdf")
            output.save(temp)
            temp.replace(destination)
