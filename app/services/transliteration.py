"""Conservative opt-in replacements in existing translated text and rich runs."""

import re
from collections import Counter
from copy import deepcopy

from app.agents.translator import LITERALS, source_hash
from app.config import StudioError

WORD = re.compile(r"(?<![\w/@\\-])[A-Za-z]{2,32}(?![\w/@\\-])")
UNITS = frozenset(
    "kg mg ml cm mm km ms Hz kHz MHz GHz MB GB TB KB USD INR EUR GBP Cr crore LPA AM PM".split()
)
LETTER_NAMES = dict(
    zip(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "ay bee see dee ee eff gee aitch eye jay kay ell em en oh pee cue ar ess tee you vee double-you ex why zee".split(),
    )
)


def pronunciation_input(word):
    return " ".join(LETTER_NAMES[c] for c in word) if word.isupper() else word


def matches(text):
    protected = [m.span() for m in LITERALS.finditer(text)]
    protected += [
        m.span() for m in re.finditer(r"\b[\w.-]+\.[A-Za-z]{2,}(?:/\S*)?", text)
    ]
    return [
        m
        for m in WORD.finditer(text)
        if m.group() not in UNITS
        and not any(m.start() < end and m.end() > start for start, end in protected)
    ]


def segments(translation):
    runs = translation.metadata_json.get("rich_text", {}).get("runs")
    return runs if runs is not None else [{"text": translation.text}]


def eligible_matches(translation):
    # Detect against the entire text so a URL / identifier split by styling stays
    # protected. Only replace words contained wholly in one style run.
    parts = segments(translation)
    full = "".join(r["text"] for r in parts)
    boundaries = []
    offset = 0
    for run in parts:
        boundaries.append((offset, offset + len(run["text"])))
        offset += len(run["text"])
    return [
        m
        for m in matches(full)
        if any(a <= m.start() and m.end() <= b for a, b in boundaries)
    ]


def candidates(entries):
    counts = Counter()
    for _, translation in entries:
        counts.update(m.group() for m in eligible_matches(translation))
    return [
        {
            "word": word,
            "count": count,
            "kind": "abbreviation" if word.isupper() else "word",
        }
        for word, count in sorted(
            counts.items(), key=lambda pair: (not pair[0].isupper(), pair[0].lower())
        )
    ]


def snapshot(entries):
    return {
        b.id: {
            "translation_id": t.id,
            "hash": source_hash(t.text),
            "runs_hash": source_hash(
                __import__("json").dumps(
                    t.metadata_json.get("rich_text", {}), sort_keys=True
                )
            ),
        }
        for b, t in entries
    }


def validate_replacement(text):
    if (
        not isinstance(text, str)
        or not text.strip()
        or len(text) > 200
        or re.search(r"[A-Za-z0-9<>\x00-\x1f]", text)
        or not any(c.isalpha() for c in text)
    ):
        raise StudioError(
            "invalid_transliteration",
            "The proposed pronunciation is not valid native-script text",
            502,
        )
    return text.strip()


def replace(translation, replacements):
    metadata = deepcopy(translation.metadata_json)
    parts = deepcopy(segments(translation))
    allowed = {
        m.start(): m for m in eligible_matches(translation) if m.group() in replacements
    }
    offset = 0
    for run in parts:
        text = run["text"]
        result = []
        last = 0
        for m in matches(text):
            original = allowed.get(offset + m.start())
            if original is not None and original.group() == m.group():
                result.extend([text[last : m.start()], replacements[m.group()]])
                last = m.end()
        result.append(text[last:])
        run["text"] = "".join(result)
        offset += len(text)
    text = "".join(r["text"] for r in parts)
    if "rich_text" in metadata and "runs" in metadata["rich_text"]:
        metadata["rich_text"]["runs"] = parts
    metadata["transliterated_words"] = {
        **metadata.get("transliterated_words", {}),
        **replacements,
    }
    return text, metadata
