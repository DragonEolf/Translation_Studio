"""Live synthetic smoke test of all native-script targets; no user PDFs."""

import asyncio
import json
import time
import unicodedata
from pathlib import Path
from types import SimpleNamespace as N

import pymupdf as fitz

from app.agents.checker import CheckerAgent
from app.agents.reorganiser import ReorganiserAgent
from app.agents.translator import TranslatorAgent
from app.config import Settings
from app.providers.bodhan import LANGUAGES, BodhanProvider
from app.services.renderer import Renderer
from app.services.text_fitter import SCRIPT_FONTS, FontRegistry

RANGES = {
    "Devanagari": (0x900, 0x97F),
    "Bengali": (0x980, 0x9FF),
    "Gujarati": (0xA80, 0xAFF),
    "Gurmukhi": (0xA00, 0xA7F),
    "Kannada": (0xC80, 0xCFF),
    "Malayalam": (0xD00, 0xD7F),
    "Tamil": (0xB80, 0xBFF),
    "Telugu": (0xC00, 0xC7F),
    "Oriya": (0xB00, 0xB7F),
    "Arabic": (0x600, 0x6FF),
    "OlChiki": (0x1C50, 0x1C7F),
    "MeeteiMayek": (0xABC0, 0xABFF),
}


async def main():
    settings = Settings()
    out = Path("artifacts/all-language-check")
    out.mkdir(parents=True, exist_ok=True)
    source = out / "source.pdf"
    with fitz.open() as doc:
        doc.new_page(width=600, height=400)
        doc.save(source)
    provider = BodhanProvider(settings.bodhan_api_key)
    agent = TranslatorAgent(provider)
    fitter = ReorganiserAgent(FontRegistry(settings.studio_font_dir or Path("fonts")))
    records = []
    for lang in sorted(LANGUAGES):
        texts = (
            [
                "The meeting will take place tomorrow.",
                "Please keep the original document safe.",
                "Invoice INV-2048 costs 1250.50",
            ]
            if lang != "en"
            else [
                "बैठक कल होगी।",
                "कृपया मूल दस्तावेज़ सुरक्षित रखें।",
                "चालान INV-2048 की राशि 1250.50 है।",
            ]
        )
        blocks = [
            N(
                id=f"{lang}-{i}",
                page_number=1,
                source_text=t,
                bbox=[30, 30 + i * 100, 570, 110 + i * 100],
                style={
                    "size": 14,
                    "color": 0,
                    "fixed_box": True,
                    "source_regions": [[30, 30 + i * 100, 570, 110 + i * 100]],
                },
                confidence=1,
            )
            for i, t in enumerate(texts)
        ]
        stats = {}
        start = time.perf_counter()
        record = {"language": lang, "source": texts}
        try:
            results = [
                r async for r in agent.translate_batch(blocks, lang, "native", stats)
            ]
            record["translations"] = [t for _, t, _, _ in results]
            record["errors"] = [e.code for _, _, _, e in results if e]
            entries = []
            for b, t, _, error in results:
                if error:
                    continue
                translation = N(text=t, target_language=lang, script="native")
                layout = N(**fitter.run(b, translation, N(width=600, height=400), []))
                entries.append((b, translation, layout))
            record["fit_statuses"] = [
                entry_layout.fit_status for _, _, entry_layout in entries
            ]
            text = "".join(t or "" for t in record["translations"])
            if lang == "en":
                record["target_script_detected"] = any(
                    c.isascii() and c.isalpha() for c in text
                )
            else:
                low, high = RANGES[SCRIPT_FONTS[lang]]
                record["target_script_detected"] = any(
                    low <= ord(c) <= high and unicodedata.category(c).startswith("L")
                    for c in text
                )
            path = out / f"{lang}.pdf"
            Renderer().render_page(source, 1, entries, path)
            record["issues"] = CheckerAgent().run(source, path, 1, entries)
            record["passed"] = (
                len(results) == 3
                and not record["errors"]
                and record["fit_statuses"] == ["fitted"] * 3
                and record["target_script_detected"]
                and not record["issues"]
            )
        except Exception as exc:
            record.update(
                passed=False, error_code=getattr(exc, "code", type(exc).__name__)
            )
        record.update(stats=stats, seconds=round(time.perf_counter() - start, 3))
        records.append(record)
        (out / "results.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2)
        )
        print(
            lang,
            "PASS" if record["passed"] else "FAIL",
            record.get("fit_statuses"),
            record.get("error_code", ""),
            stats,
            flush=True,
        )
    print("TOTAL:", sum(r["passed"] for r in records), "/", len(records), flush=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required for paid provider calls")
    asyncio.run(main())
