import pymupdf as fitz
from app.config import StudioError
from app.providers.ocr import validate_response


class OCRReaderAgent:
    def image(self, source, number, dpi):
        with fitz.open(source) as pdf:
            pixmap = pdf[number-1].get_pixmap(dpi=dpi,alpha=False)
            return pixmap.tobytes("png"), pixmap.width, pixmap.height

    def blocks(self, document_id, number, metadata, response, pixel_width, pixel_height):
        validate_response(response)
        results = []
        for index, raw in enumerate(response["blocks"]):
            if not raw["text"].strip():
                continue
            x0,y0,x1,y1 = raw["bbox_xyxy"]
            if x0 < 0 or y0 < 0 or x1 > pixel_width+1 or y1 > pixel_height+1:
                raise StudioError("invalid_ocr_response", "OCR coordinates exceed page image boundaries",502)
            bbox = [x0*metadata["width"]/pixel_width,y0*metadata["height"]/pixel_height,min(x1,pixel_width)*metadata["width"]/pixel_width,min(y1,pixel_height)*metadata["height"]/pixel_height]
            label = raw.get("label", "Paragraph").lower()
            kind = "heading" if "title" in label else "table" if label == "table" else "list_item" if label == "list" else "caption" if "caption" in label else label if label in {"header","footer","footnote"} else "paragraph"
            lines = max(1,len(raw["text"].splitlines()))
            size = max(8,min(24,(bbox[3]-bbox[1])/lines*.7))
            results.append({"id":f"{document_id}-p{number}-ocr{index}","document_id":document_id,"page_number":number,"type":kind,"reading_order":raw.get("order",index),"bbox":bbox,"source_text":raw["text"],"style":{"font":"OCR-estimated","size":size,"color":0,"bold":kind=="heading","ocr":True,"complex_table":kind=="table","unsupported_ocr":label in {"equation","expression","chart","diagram","image"}},"confidence":raw.get("conf",0)})
        return results
