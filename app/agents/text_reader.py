import pymupdf as fitz
from app.services.underlines import attach_underlines


class TextReaderAgent:
    def run(self, path):
        with fitz.open(path) as pdf:
            results = []
            for page in pdf:
                data = page.get_text("dict", sort=True, flags=fitz.TEXTFLAGS_DICT | fitz.TEXT_COLLECT_STYLES)
                # Spaces used as manual indentation are not visible text. Keep
                # the full bbox for redaction, but measure visible character x
                # bounds for positioning and alignment inference.
                padded=[s for b in data['blocks'] if b['type']==0 for line in b['lines']
                        for s in line['spans'] if s['text'].strip() and s['text']!=s['text'].strip()]
                if padded:
                    raw=page.get_text('rawdict',flags=(fitz.TEXTFLAGS_DICT | fitz.TEXT_COLLECT_STYLES) & ~fitz.TEXT_PRESERVE_IMAGES)
                    visible={}
                    for b in raw['blocks']:
                        if b['type']!=0:
                            continue
                        for line in b['lines']:
                            for span in line['spans']:
                                chars=[c for c in span['chars'] if not c['c'].isspace()]
                                if chars:
                                    visible[(tuple(span['bbox']),span['font'])]=(min(c['bbox'][0] for c in chars),max(c['bbox'][2] for c in chars))
                    for span in padded:
                        bounds=visible.get((tuple(span['bbox']),span['font']))
                        if bounds:
                            span['ink_bbox']=[bounds[0],span['bbox'][1],bounds[1],span['bbox'][3]]
                attach_underlines(page, data)
                data["tables"] = []
                try:
                    for table in page.find_tables().tables:
                        cells = []
                        extracted = table.extract()
                        for row_number, row in enumerate(table.rows):
                            for column, bbox in enumerate(row.cells):
                                if bbox:
                                    text = extracted[row_number][column] or ""
                                    if text.strip():
                                        cells.append({"bbox": list(bbox), "text": text})
                        data["tables"].append({"bbox": list(table.bbox), "cells": cells, "complex": any(cell is None for row in table.rows for cell in row.cells)})
                except Exception:
                    # Text extraction remains available if table detection fails.
                    data["table_detection_failed"] = True
                results.append(data)
            return results
