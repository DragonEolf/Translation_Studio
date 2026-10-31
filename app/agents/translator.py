"""Translate validated XML batches with protected literals and bounded passage recovery."""

import hashlib
import html
import re
import unicodedata
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from types import SimpleNamespace

from app.config import StudioError
from app.services.rich_text import (
    encode_groups,
    literal_source,
    literal_target,
    rich,
    source_runs,
    validate_groups,
)

LITERALS = re.compile(
    r"https?://[^\s]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b[A-Z][A-Z0-9_-]*\d[A-Z0-9_-]*\b|\b\d+(?:[.,:/-]\d+)*(?:%)?"
)


def source_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def literal_only(text):
    return not LITERALS.sub("", text).strip(" \n\t.,;:/()%+-|")


def encode_batch(blocks, nonce):
    ids = [f"{nonce}_{i:03}" for i in range(len(blocks))]
    text = "\n".join(
        f'<span id="{identity}">{html.escape(b.source_text, quote=False)}</span>'
        for identity, b in zip(ids, blocks)
    )
    return ids, text


def validate_batch(output, ids, blocks):
    """Require exact ordered IDs and text-only XML; decode entities once."""
    try:
        if not isinstance(output, str) or "<!" in output or "<?" in output:
            raise ValueError()
        root = ET.fromstring("<batch>" + output + "</batch>")
        if (root.text or "").strip() or len(root) != len(ids):
            raise ValueError()
        texts = []
        for child, identity, block in zip(root, ids, blocks):
            if (
                child.tag != "span"
                or child.attrib != {"id": identity}
                or len(child)
                or (child.tail or "").strip()
                or not (child.text or "").strip()
            ):
                raise ValueError()
            texts.append(validate_literals(block.source_text, child.text.strip()))
        return texts
    except (ET.ParseError, ValueError, TypeError, StudioError) as exc:
        raise StudioError(
            "invalid_translation_batch",
            "Translation batch has invalid tags, IDs, content, or protected values",
            502,
        ) from exc


def normalize_digits(text):
    return "".join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in text)


def validate_literals(source, translated):
    translated = normalize_digits(translated)
    expected = Counter(LITERALS.findall(source))
    actual = Counter(LITERALS.findall(translated))
    if any(actual[value] < count for value, count in expected.items()):
        raise StudioError(
            "protected_value_changed", "Translation changed a protected source value"
        )
    return translated


def protect(text):
    literals = []

    def replace(match):
        literals.append(match.group())
        return f"ZXQKEEP{len(literals) - 1}QXZ"

    return LITERALS.sub(replace, text), literals


def restore(text, literals):
    for i, literal in enumerate(literals):
        token = f"ZXQKEEP{i}QXZ"
        if text.count(token) != 1:
            raise StudioError(
                "protected_value_changed", "Translation altered a protected placeholder"
            )
        text = text.replace(token, literal)
    return text


def split_passage(text):
    """Split near the middle without cutting identifiers or OCR math notation."""
    protected = [(m.start(), m.end()) for m in LITERALS.finditer(text)]
    protected += [
        (m.start(), m.end())
        for m in re.finditer(
            r"\$\$[\s\S]*?\$\$|\$[^$\n]*\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]", text
        )
    ]
    candidates = [
        m
        for m in re.finditer(r"\s+", text)
        if text[: m.start()].strip()
        and text[m.end() :].strip()
        and not any(m.start() < end and m.end() > start for start, end in protected)
    ]
    if not candidates:
        return None
    # Prefer sentences, clauses, and physical lines when they produce balanced
    # children. Otherwise use a word boundary; never cut a Unicode word in half.
    natural = [
        m
        for m in candidates
        if 0.2 * len(text) <= m.start() <= 0.8 * len(text)
        and ("\n" in m.group() or text[m.start() - 1] in ".!?;:।॥")
    ]
    boundary = min(
        natural or candidates,
        key=lambda m: abs((m.start() + m.end()) / 2 - len(text) / 2),
    )
    return text[: boundary.start()], boundary.group(), text[boundary.end() :]


class TranslatorAgent:
    MAX_RECOVERY_CALLS = 31
    MAX_RECOVERY_DEPTH = 5

    def __init__(self, provider):
        self.provider = provider

    def batches(self, blocks, size=30, max_chars=6000):
        batch = []
        for block in blocks:
            # Count escaped XML, not just source text. The fixed-length nonce
            # has the same length as the live one used by translate_batch.
            candidate = batch + [block]
            if batch and (
                block.page_number != batch[0].page_number
                or len(candidate) > size
                or bool(rich(block)) != bool(rich(batch[0]))
                or len(
                    (encode_groups if rich(block) else encode_batch)(
                        candidate, "B" + "0" * 12
                    )[1]
                )
                > max_chars
            ):
                yield batch
                batch = []
            batch.append(block)
        if batch:
            yield batch

    async def translate_batch(self, blocks, language, script, stats=None):
        """Yield validated per-span results, splitting only content failures.

        Provider/auth/network errors propagate; splitting those would amplify
        outages. Results are yielded so successful halves can commit immediately.
        """
        stats = stats if stats is not None else {}
        if not blocks:
            return
        if rich(blocks[0]):
            async for result in self.translate_rich_batch(
                blocks, language, script, stats
            ):
                yield result
            return
        if len(blocks) == 1:
            stats["individual_calls"] = stats.get("individual_calls", 0) + 1
            block = blocks[0]
            try:
                text, metadata = await self.run(block, language, script)
                yield block, text, metadata, None
            except StudioError as exc:
                if exc.code not in {"translation_truncated", "protected_value_changed"}:
                    raise
                yield block, None, None, exc
            return
        nonce = "B" + uuid.uuid4().hex[:12]
        ids, payload = encode_batch(blocks, nonce)
        stats["batch_calls"] = stats.get("batch_calls", 0) + 1
        try:
            body = await self.provider.translate(payload, language, script)
        except StudioError as exc:
            if exc.code not in {"translation_truncated", "protected_value_changed"}:
                raise
            body = {"truncated": True}
        usage = body.get("usage", {})
        stats.setdefault("batch_requests", []).append(
            {
                "input_chars": len(payload),
                "finish_reason": body.get("finish_reason"),
                "truncated": bool(body.get("truncated")),
                "usage": dict(usage),
            }
        )
        stats["batch_tokens"] = stats.get("batch_tokens", 0) + usage.get(
            "total_tokens", 0
        )
        try:
            if body.get("truncated"):
                raise StudioError(
                    "invalid_translation_batch", "Translation batch was truncated", 502
                )
            texts = validate_batch(body.get("translation"), ids, blocks)
        except StudioError:
            stats["invalid_batches"] = stats.get("invalid_batches", 0) + 1
            middle = len(blocks) // 2
            for half in (blocks[:middle], blocks[middle:]):
                async for result in self.translate_batch(half, language, script, stats):
                    yield result
            return
        # Store each API usage once, rather than multiplying it by span count.
        for index, (block, text) in enumerate(zip(blocks, texts)):
            yield (
                block,
                text,
                {
                    "source_hash": source_hash(block.source_text),
                    "protected_values": LITERALS.findall(block.source_text),
                    "usage": usage if index == 0 else {},
                    "batch_id": nonce,
                    "batch_size": len(blocks),
                },
                None,
            )

    async def translate_rich_batch(self, blocks, language, script, stats):
        nonce = "R" + uuid.uuid4().hex[:12]
        ids, payload = encode_groups(blocks, nonce)
        stats["batch_calls"] = stats.get("batch_calls", 0) + 1
        body = await self.provider.translate(payload, language, script)
        usage = body.get("usage", {})
        stats.setdefault("batch_requests", []).append(
            {
                "input_chars": len(payload),
                "finish_reason": body.get("finish_reason"),
                "truncated": bool(body.get("truncated")),
                "usage": dict(usage),
            }
        )
        stats["batch_tokens"] = stats.get("batch_tokens", 0) + usage.get(
            "total_tokens", 0
        )
        try:
            if body.get("truncated"):
                raise StudioError(
                    "invalid_rich_translation", "Translation was truncated", 502
                )
            parsed = validate_groups(body.get("translation"), ids, blocks)
        except StudioError:
            stats["invalid_batches"] = stats.get("invalid_batches", 0) + 1
            if len(blocks) > 1:
                middle = len(blocks) // 2
                for half in (blocks[:middle], blocks[middle:]):
                    async for result in self.translate_rich_batch(
                        half, language, script, stats
                    ):
                        yield result
                return
            # Last resort preserves style ownership deterministically. Context
            # and word order are weaker, so the pipeline reports a review warning.
            block = blocks[0]
            runs = []
            fallback_usage = dict(usage)
            run_diagnostics = []
            try:
                for run in source_runs(block):
                    if run["text"].strip():
                        if not literal_only(run["text"]):
                            stats["individual_calls"] = (
                                stats.get("individual_calls", 0) + 1
                            )
                        text, metadata = await self.run(
                            SimpleNamespace(source_text=run["text"]), language, script
                        )
                        run_diagnostics.append(
                            {"run_id": run["id"], **metadata["translation_diagnostics"]}
                        )
                        for key, value in metadata.get("usage", {}).items():
                            if isinstance(value, (int, float)):
                                fallback_usage[key] = fallback_usage.get(key, 0) + value
                        leading = " " if run["text"][:1].isspace() else ""
                        trailing = " " if run["text"][-1:].isspace() else ""
                        runs.append({**run, "text": leading + text.strip() + trailing})
                    else:
                        runs.append(run)
                text = "".join(r["text"] for r in runs)
                validate_literals(literal_source(block), literal_target(runs))
            except StudioError as exc:
                if exc.code not in {"translation_truncated", "protected_value_changed"}:
                    raise
                yield block, None, None, exc
                return
            stats["rich_fallbacks"] = stats.get("rich_fallbacks", 0) + 1
            yield (
                block,
                text,
                {
                    "source_hash": source_hash(block.source_text),
                    "usage": fallback_usage,
                    "rich_text": {"runs": runs, "fallback": True},
                    "translation_diagnostics": {
                        "runs": run_diagnostics,
                        "splits": sum(d["splits"] for d in run_diagnostics),
                    },
                },
                None,
            )
            return
        for index, (block, (text, runs)) in enumerate(zip(blocks, parsed)):
            yield (
                block,
                text,
                {
                    "source_hash": source_hash(block.source_text),
                    "usage": usage if index == 0 else {},
                    "batch_id": nonce,
                    "batch_size": len(blocks),
                    "rich_text": {"runs": runs, "fallback": False},
                },
                None,
            )

    async def run(self, block, language, script):
        # State belongs to this passage, never the shared agent: concurrent jobs
        # must not share request budgets or diagnostics.
        state = {"requests": [], "usage": {}, "splits": 0}
        try:
            text, metadata = await self._recover(block, language, script, state, 0)
        except StudioError as exc:
            exc.diagnostics = {
                "requests": state["requests"],
                "splits": state["splits"],
                "usage": state["usage"],
            }
            if exc.code == "translation_truncated":
                exc.message = (
                    f"Translation remained incomplete after {len(state['requests'])} requests "
                    f"and {state['splits']} passage splits; source retained for review."
                )
                exc.args = (exc.message,)
            raise
        return text, {
            **metadata,
            "source_hash": source_hash(block.source_text),
            "usage": state["usage"],
            "translation_diagnostics": {
                "requests": state["requests"],
                "splits": state["splits"],
            },
        }

    async def _recover(self, block, language, script, state, depth):
        try:
            return await self._run_once(block, language, script, state)
        except StudioError as exc:
            if exc.code != "translation_truncated" or depth >= self.MAX_RECOVERY_DEPTH:
                raise
            pieces = split_passage(block.source_text)
            if pieces is None or len(state["requests"]) >= self.MAX_RECOVERY_CALLS:
                raise
        left, separator, right = pieces
        state["splits"] += 1
        chunks = []
        for source in (left, right):
            translated, _ = await self._recover(
                SimpleNamespace(source_text=source), language, script, state, depth + 1
            )
            chunks.append(translated.strip())
        text = validate_literals(block.source_text, separator.join(chunks))
        return text, {"protected_values": LITERALS.findall(block.source_text)}

    async def _request(self, text, language, script, state):
        if len(state["requests"]) >= self.MAX_RECOVERY_CALLS:
            raise StudioError(
                "translation_truncated", "Passage recovery request budget exhausted"
            )
        diagnostic = {"input_chars": len(text)}
        state["requests"].append(diagnostic)
        try:
            body = await self.provider.translate(text, language, script)
        except StudioError as exc:
            diagnostic["error_code"] = exc.code
            raise
        diagnostic.update(
            {
                "finish_reason": body.get("finish_reason"),
                "truncated": bool(body.get("truncated")),
                "usage": dict(body.get("usage", {})),
            }
        )
        for key, value in body.get("usage", {}).items():
            if isinstance(value, (int, float)):
                state["usage"][key] = state["usage"].get(key, 0) + value
        return body

    async def _run_once(self, block, language, script, state):
        provenance = {"source_hash": source_hash(block.source_text)}
        if literal_only(block.source_text):
            metadata = {
                **provenance,
                "unchanged_literal": True,
                "usage": {},
                "protected_values": LITERALS.findall(block.source_text),
            }
            if rich(block):
                metadata["rich_text"] = {"runs": source_runs(block), "fallback": False}
            return block.source_text, metadata
        protected, literals = protect(block.source_text)
        body = await self._request(protected, language, script, state)
        if body.get("truncated"):
            raise StudioError(
                "translation_truncated",
                "Translation was cut off; split or review the block",
            )
        try:
            translated = restore(body["translation"], literals)
        except StudioError:
            retry = await self._request(block.source_text, language, script, state)
            if retry.get("truncated"):
                raise StudioError("translation_truncated", "Translation was cut off")
            try:
                translated = validate_literals(block.source_text, retry["translation"])
            except StudioError:
                # Final bounded fallback: translate prose runs while copying literals
                # verbatim. This trades some context for guaranteed identifier integrity.
                pieces = []
                position = 0
                for match in list(LITERALS.finditer(block.source_text)) + [None]:
                    end = match.start() if match else len(block.source_text)
                    prose = block.source_text[position:end]
                    if prose.strip() and re.search(r"\w", prose):
                        part = await self._request(
                            prose.strip(), language, script, state
                        )
                        if part.get("truncated"):
                            raise StudioError(
                                "translation_truncated",
                                "Literal-preserving translation was cut off",
                            )
                        leading = prose[: len(prose) - len(prose.lstrip())]
                        trailing = prose[len(prose.rstrip()) :]
                        pieces.append(leading + part["translation"].strip() + trailing)
                    else:
                        pieces.append(prose)
                    if match:
                        pieces.append(match.group())
                        position = match.end()
                translated = validate_literals(block.source_text, "".join(pieces))
        return translated, {
            **provenance,
            "usage": body.get("usage", {}),
            "protected_values": literals,
        }
