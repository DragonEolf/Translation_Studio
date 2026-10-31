"""Synthetic-only sparse formatting test. No document or database access."""

import asyncio
import html
import json
import time
import xml.etree.ElementTree as ET

from app.agents.translator import validate_literals
from app.config import Settings
from app.providers.bodhan import BodhanProvider
from scripts.inline_layout_experiment import OUT, synthetic


def encode_sparse(groups, tag="b"):
    return "\n".join(
        '<p id="'
        + g["id"]
        + '">'
        + "".join(
            (
                "<"
                + tag
                + ' id="'
                + s["id"]
                + '">'
                + html.escape(s["text"], quote=False)
                + "</"
                + tag
                + ">"
            )
            if s["bold"]
            else html.escape(s["text"], quote=False)
            for s in g["spans"]
        )
        + "</p>"
        for g in groups
    )


def validate_sparse(output, groups, tag="b"):
    if "<!" in output or "<?" in output:
        raise ValueError("unsafe XML")
    root = ET.fromstring("<batch>" + output + "</batch>")
    expected = {g["id"]: g for g in groups}
    result = {}
    if (root.text or "").strip() or len(root) != len(groups):
        raise ValueError("outside text/paragraph count")
    for p in root:
        pid = p.get("id")
        if (
            p.tag != "p"
            or pid not in expected
            or p.attrib != {"id": pid}
            or pid in result
            or (p.tail or "").strip()
        ):
            raise ValueError("paragraph ID")
        g = expected[pid]
        styled = {s["id"]: s for s in g["spans"] if s["bold"]}
        seen, runs = set(), []
        if p.text:
            runs.append({"id": pid + "_r0", "text": p.text, "bold": False})
        for s in p:
            sid = s.get("id")
            if (
                s.tag != tag
                or sid not in styled
                or s.attrib != {"id": sid}
                or sid in seen
                or len(s)
                or not (s.text or "").strip()
            ):
                raise ValueError("formatting ID/content")
            validate_literals(styled[sid]["text"], s.text)
            seen.add(sid)
            runs.append({**styled[sid], "text": s.text})
            if s.tail:
                runs.append(
                    {"id": pid + "_r" + str(len(runs)), "text": s.tail, "bold": False}
                )
        if seen != set(styled):
            raise ValueError("missing formatting ID")
        validate_literals(
            "".join(s["text"] for s in g["spans"]), "".join(s["text"] for s in runs)
        )
        result[pid] = runs
    return result


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    provider = BodhanProvider(Settings().bodhan_api_key)
    groups, records = synthetic(), []
    for tag in ["b", "span"]:
        for lang in ["hi", "ta"]:
            payload = encode_sparse(groups, tag)
            start = time.perf_counter()
            body = await provider.translate(payload, lang, "native")
            rec = dict(
                tag=tag,
                language=lang,
                input=payload,
                output=body["translation"],
                seconds=round(time.perf_counter() - start, 2),
                truncated=body["truncated"],
            )
            try:
                if body["truncated"]:
                    raise ValueError("truncated")
                result = validate_sparse(body["translation"], groups, tag)
                rec.update(
                    valid=True,
                    paragraphs=len(result),
                    styled_spans=sum(s["bold"] for rs in result.values() for s in rs),
                )
            except Exception as exc:
                rec.update(valid=False, error=str(exc))
            records.append(rec)
            (OUT / "sparse-api-results.json").write_text(
                json.dumps(records, ensure_ascii=False, indent=2)
            )
            print(
                json.dumps(
                    {k: v for k, v in rec.items() if k not in {"input", "output"}}
                ),
                flush=True,
            )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required for paid provider calls")
    asyncio.run(main())
