from types import SimpleNamespace

import numpy as np
import pymupdf as fitz
import pytest

from app.agents.ocr_reader import OCRReaderAgent
from app.config import StudioError
from app.providers.ocr import validate_response
from app.services.renderer import Renderer
from app.services.scan_background import ScanBackground, polygon_mask


def response(polygon):
    return {
        "choices": [{"finish_reason": "stop"}],
        "blocks": [
            {"bbox_xyxy": [20, 20, 180, 100], "text": "scan", "polygon_xy": polygon}
        ],
    }


def test_polygon_coordinates_are_scaled_and_preserved():
    polygon = [[20, 40], [160, 20], [180, 80], [40, 100]]
    block = OCRReaderAgent().blocks(
        "doc", 1, {"width": 100, "height": 60}, response(polygon), 200, 120
    )[0]
    assert block["style"]["source_polygon"] == [[10, 20], [80, 10], [90, 40], [20, 50]]


def test_outline_with_more_than_four_vertices_preserves_concave_notch():
    polygon = [[0, 0], [10, 0], [10, 4], [4, 4], [4, 10], [0, 10]]
    assert polygon_mask(np.array([2, 8, 8]), np.array([8, 2, 8]), polygon).tolist() == [
        True,
        True,
        False,
    ]


@pytest.mark.parametrize(
    "polygon",
    [
        [],
        [[20, 20], [30, 30]],
        [[20, 20], [30, 30], [40, 40]],
        [[0, 0], [80, 20], [20, 80]],
        [[20, 20], [float("nan"), 30], [40, 80]],
        [[20, 20], [180, 100], [180, 20], [20, 80]],
        [[20, 20], [180, 20], [180, 100], [20, 20]],
    ],
)
def test_invalid_polygon_is_rejected(polygon):
    with pytest.raises(StudioError):
        validate_response(response(polygon))


def test_background_follows_local_lighting_and_ignores_ink():
    with fitz.open() as pdf:
        page = pdf.new_page(width=240, height=160)
        y, x = np.mgrid[:160, :240]
        rgb = np.stack(
            (205 + x * 0.12, 195 + x * 0.12, 175 + x * 0.12), axis=-1
        ).astype(np.uint8)
        pix = fitz.Pixmap(fitz.csRGB, 240, 160, rgb.tobytes(), False)
        page.insert_image(page.rect, pixmap=pix)
        page.insert_text((60, 80), "Dark handwriting", fontsize=12, color=(0, 0, 0.3))
        background = ScanBackground(page, [[40, 40, 200, 120]])
        _, patch = background.patch([40, 40, 200, 120])
        samples = np.frombuffer(patch.samples, np.uint8).reshape(
            patch.height, patch.width, 3
        )
        assert samples[:, -10:, 0].mean() > samples[:, :10, 0].mean() + 10
        assert (
            np.max(
                np.abs(
                    samples[samples.shape[0] // 2, samples.shape[1] // 2].astype(float)
                    - rgb[80, 120]
                )
            )
            < 5
        )


def test_renderer_preserves_pixels_outside_polygon_and_searchable_translation(tmp_path):
    source, dest = tmp_path / "scan.pdf", tmp_path / "rendered.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page(width=240, height=160)
        page.draw_rect(page.rect, fill=(0.82, 0.77, 0.65), color=None)
        page.draw_rect((40, 40, 200, 120), fill=(0.1, 0.2, 0.4), color=None)
        pdf.save(source)
    block = SimpleNamespace(
        bbox=[40, 40, 200, 120],
        style={
            "ocr": True,
            "size": 10,
            "color": 0,
            "source_polygon": [[40, 65], [180, 40], [200, 95], [60, 120]],
        },
    )
    layout = SimpleNamespace(
        bbox=[65, 65, 175, 95], font="sans-serif", font_size=10, fit_status="fitted"
    )
    Renderer().render_page(
        source, 1, [(block, SimpleNamespace(text="Translated"), layout)], dest
    )
    with fitz.open(dest) as pdf:
        page = pdf[0]
        assert "Translated" in page.get_text()
        pix = page.get_pixmap(colorspace=fitz.csRGB)
        assert pix.pixel(43, 43) == (25, 51, 102)  # Inside bbox, outside polygon.
        assert (
            np.max(np.abs(np.array(pix.pixel(80, 105)) - np.array([209, 196, 165]))) < 3
        )


def test_background_near_page_edge_has_safe_fallback():
    with fitz.open() as pdf:
        page = pdf.new_page(width=30, height=30)
        _, pix = ScanBackground(page, [[0, 0, 30, 30]]).patch([0, 0, 30, 30])
        assert pix.pixel(0, 0) == (255, 255, 255)
