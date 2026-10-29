"""Locate fonts for the model-written document program."""
from pathlib import Path


from app.services.text_fitter import FontRegistry


def font_paths(directory, language):
    fonts = FontRegistry(directory)
    result = {"en": {}, language: {}}
    for lang in result:
        for bold in (False, True):
            path = fonts.resolve(lang, "native", bold=bold)
            if path in (None, "missing"):
                raise ValueError(f"Missing {'bold' if bold else 'regular'} font for {lang}; set --font-dir")
            result[lang][bold] = Path(path).resolve()
    return result
