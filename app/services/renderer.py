from pathlib import Path
import pymupdf as fitz
from app.services.text_fitter import markup


class Renderer:
    def render_page(self, source, number, entries, destination):
        with fitz.open(source) as original, fitz.open() as output:
            output.insert_pdf(original, from_page=number-1, to_page=number-1)
            page = output[0]
            fitted = [(b, t, layout) for b, t, layout in entries if layout.fit_status == "fitted"]
            for block, _, _ in fitted:
                if block.style.get("ocr"):
                    page.draw_rect(fitz.Rect(block.bbox),color=None,fill=(1,1,1),overlay=True)
                else:
                    for region in block.style.get("source_regions",[block.bbox]):
                        page.add_redact_annot(fitz.Rect(region), fill=False)
            if fitted:
                page.apply_redactions(images=0, graphics=0, text=0)
            for block, translation, layout in fitted:
                font_path = None if layout.font == "sans-serif" else layout.font
                content, css = markup(translation.text, layout.font_size, block.style["color"], font_path, block.style.get("bold", False),block.style.get("align","left"),block.style.get("italic",False))
                if all(ord(c)<0x300 for c in translation.text):
                    spare,_=page.insert_htmlbox(fitz.Rect(layout.bbox),content,css=css,archive=fitz.Archive(str(Path(font_path).parent)) if font_path else None,scale_low=1)
                    if spare<0:
                        raise RuntimeError("Validated layout failed while rendering")
                    continue
                with fitz.open() as shaped:
                    shape_page=shaped.new_page(width=page.rect.width,height=page.rect.height)
                    spare,_=shape_page.insert_htmlbox(fitz.Rect(layout.bbox),content,css=css,archive=fitz.Archive(str(Path(font_path).parent)) if font_path else None,scale_low=1)
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
                stretch=rect.width/fitz.get_text_length("x",fontname="helv",fontsize=size)
                before=set(page.get_contents())
                page.insert_text(origin,"x",fontsize=size,fontname="helv",render_mode=3,morph=(origin,fitz.Matrix(stretch,1)))
                actual="FEFF"+translation.text.encode("utf-16-be").hex().upper()
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
