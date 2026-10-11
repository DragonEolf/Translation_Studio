import pymupdf as fitz


class TextReaderAgent:
    def run(self, path):
        with fitz.open(path) as pdf:
            results = []
            for page in pdf:
                data = page.get_text("dict", sort=True)
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
