"""Identify ambiguous small math detections overlapping a larger OCR passage.

Keep every source block for review; do not silently delete uncertain OCR text.
"""
import re

import pymupdf as fitz


def overlap_reviews(blocks):
    def field(block, key):
        return block[key] if isinstance(block, dict) else getattr(block, key)

    reviews = {}
    for block in blocks:
        style = field(block, 'style')
        text = field(block, 'source_text').strip()
        if not style.get('ocr') or len(text) > 64 or not re.fullmatch(r'\$\$[^$]+\$\$|\$[^$]+\$', text):
            continue
        rect = fitz.Rect(field(block, 'bbox'))
        area = rect.get_area()
        if area <= 0:
            continue
        for parent in blocks:
            if field(parent, 'id') == field(block, 'id'):
                continue
            other_style = field(parent, 'style')
            if not other_style.get('ocr') or other_style.get('unsupported_ocr') or other_style.get('complex_table'):
                continue
            if len(field(parent, 'source_text').strip()) < 40:
                continue
            other = fitz.Rect(field(parent, 'bbox'))
            if area <= other.get_area()*.15 and (rect & other).get_area()/area >= .75:
                reviews[field(block, 'id')] = field(parent, 'id')
                break
    return reviews
