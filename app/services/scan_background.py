"""Estimate replacement paper from the unmodified scan, never from prior fills."""
import math

import numpy as np
import pymupdf as fitz


def polygon_mask(x, y, polygon):
    """Even/odd point-in-polygon test, supporting quadrilaterals and curved outlines."""
    inside = np.zeros(np.broadcast_shapes(x.shape, y.shape), dtype=bool)
    previous = polygon[-1]
    for current in polygon:
        ax, ay = previous
        bx, by = current
        if ay != by:
            inside ^= ((ay > y) != (by > y)) & (x < (bx-ax)*(y-ay)/(by-ay)+ax)
        previous = current
    return inside


class ScanBackground:
    def __init__(self, page, regions):
        self.page_rect = page.rect
        pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), colorspace=fitz.csRGB, alpha=False)
        self.rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
        self.regions = [fitz.Rect(region) for region in regions]

    def patch(self, bbox, polygon=None):
        rect = fitz.Rect(bbox) & self.page_rect
        if rect.is_empty:
            raise ValueError("Empty OCR background region")
        # A nearby band follows local lighting and avoids including the whole photo.
        band = (rect + (-12, -12, 12, 12)) & self.page_rect
        x0, y0 = max(0, math.floor(band.x0)), max(0, math.floor(band.y0))
        x1, y1 = min(self.rgb.shape[1], math.ceil(band.x1)), min(self.rgb.shape[0], math.ceil(band.y1))
        yy, xx = np.mgrid[y0:y1, x0:x1]
        xx, yy = xx + .5, yy + .5
        available = np.ones(xx.shape, bool)
        for other in self.regions:
            if other.intersects(band):
                available &= ~((xx >= other.x0) & (xx <= other.x1) & (yy >= other.y0) & (yy <= other.y1))
        pixels = self.rgb[y0:y1, x0:x1].astype(float)
        # At page edges or in dense layouts use bright paper inside the crop too.
        if available.sum() < 32:
            available = np.ones(xx.shape, bool)
        samples = pixels[available]
        sx, sy = xx[available], yy[available]
        luminance = samples.mean(axis=1)
        # Dark ink, colored ruling and table backgrounds should not tint the fill.
        keep = (luminance >= np.percentile(luminance, 45)) & (luminance <= np.percentile(luminance, 98))
        if keep.sum() < 16:
            keep = luminance >= np.median(luminance)
        samples, sx, sy = samples[keep], sx[keep], sy[keep]
        median = np.median(samples, axis=0)
        chroma = samples - samples.mean(axis=1, keepdims=True)
        chroma_distance = np.linalg.norm(chroma - (median-median.mean()), axis=1)
        keep = chroma_distance <= max(12, float(np.percentile(chroma_distance, 70)))
        samples, sx, sy = samples[keep], sx[keep], sy[keep]
        # Fit a gentle lighting plane; constrain predictions to nearby paper colours
        # so a dark edge cannot create an extreme extrapolated patch.
        design = np.column_stack(((sx-rect.x0)/rect.width, (sy-rect.y0)/rect.height, np.ones(len(sx))))
        width, height = max(1, math.ceil(rect.width*2)), max(1, math.ceil(rect.height*2))
        py, px = np.mgrid[:height, :width]
        nx, ny = (px+.5)/width, (py+.5)/height
        if len(samples) >= 32 and np.linalg.matrix_rank(design) == 3:
            coefficients = np.linalg.lstsq(design, samples, rcond=None)[0]
            background = np.stack((nx, ny, np.ones_like(nx)), axis=-1) @ coefficients
            background = np.clip(background, np.percentile(samples, 5, axis=0)-4, np.percentile(samples, 95, axis=0)+4)
        else:
            background = np.broadcast_to(median, (height, width, 3))
        background = np.rint(np.clip(background, 0, 255)).astype(np.uint8)
        if polygon:
            alpha = polygon_mask(rect.x0+nx*rect.width, rect.y0+ny*rect.height, polygon).astype(np.uint8)*255
            # MuPDF expects premultiplied alpha samples.
            rgba = np.dstack((background*(alpha[..., None]//255), alpha))
            pixmap = fitz.Pixmap(fitz.csRGB, width, height, rgba.tobytes(), True)
        else:
            pixmap = fitz.Pixmap(fitz.csRGB, width, height, background.tobytes(), False)
        return rect, pixmap

    def paint(self, page, block):
        rect, pixmap = self.patch(block.bbox, block.style.get("source_polygon"))
        page.insert_image(rect, pixmap=pixmap, overlay=True, keep_proportion=False)
