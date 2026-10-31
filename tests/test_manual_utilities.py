"""Manual utilities must be safe to import from tests and diagnostic tools."""

import importlib
import urllib.request


def test_font_downloader_import_does_not_download_or_write(monkeypatch):
    def unexpected_download(*args, **kwargs):
        raise AssertionError(
            "Importing the font downloader must not access the network"
        )

    monkeypatch.setattr(urllib.request, "urlopen", unexpected_download)
    module = importlib.import_module("scripts.download_fonts")
    importlib.reload(module)
    assert callable(module.main)
