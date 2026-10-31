"""Paragraph geometry, local styles, and XLIFF 2.2 interchange.

The provider sees only paragraph IDs and sparse inline codes. Geometry and
original style definitions never come from provider output.
"""

import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from statistics import median

import pymupdf as fitz

from app.config import StudioError
from app.services.layout_policy import allocate_flow_space, annotate_policy, plan_chunks
from app.services.layout_policy import role as chunk_role

XLIFF_NS = "urn:oasis:names:tc:xliff:document:2.2"


def rich(block):
    return getattr(block, "style", {}).get("rich_text")


def format_hash(block):
    ir = rich(block)
    if not ir:
        return None
    data = {key: ir[key] for key in ("base_style_id", "styles", "runs")}
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def style_key(style):
    return (
        bool(style.get("bold")),
        bool(style.get("italic")),
        int(style.get("color", 0)),
        round(float(style.get("size", 12)), 1),
        style.get("font", ""),
        bool(style.get("superscript")),
        bool(style.get("underline")),
    )


def source_alignment(lines, style, page):
    """Infer centering from original line geometry, before expanding glyph boxes.

    PDF text has positions rather than paragraph alignment properties. Require
    every source line to share the container center; bullets remain left aligned.
    """
    if any(re.match(r"^\s*(?:[●•▪*-]|\d+[.)])", line["text"]) for line in lines):
        return "left"
    cell = lines[0]["cell"]
    container = (
        fitz.Rect(cell) if cell else fitz.Rect(0, 0, page["width"], page["height"])
    )
    tolerance = min(
        5.0 if cell else 3.0, max(1.0, float(style["size"]) * (0.5 if cell else 0.2))
    )
    center = (container.x0 + container.x1) / 2
    if all(
        abs((line["bbox"][0] + line["bbox"][2]) / 2 - center) <= tolerance
        and container.width - (line["bbox"][2] - line["bbox"][0]) > tolerance * 4
        for line in lines
    ):
        return "center"
    return "left"


AUTO_PARAGRAPH_MIN_SPANS = 4


def group_blocks(blocks, pages, min_spans=1):
    """Build source paragraphs using a conservative, auditable layout policy.

    Every source span keeps its identity, geometry and style. Confident physical
    continuations can cross extraction blocks; independent regions stay anchored.
    """
    raw_text = {}
    for pn, page in enumerate(pages, 1):
        raw = [b for b in page["blocks"] if b["type"] == 0]
        for bi, b in enumerate(raw):
            for li, line in enumerate(b["lines"]):
                for si, span in enumerate(line["spans"]):
                    raw_text[(pn, bi, li, si)] = span["text"]

    def span_text(pn, bi, li, si, block, previous=None):
        original = re.search(r"-b(\d+)-l(\d+)-s(\d+)$", block["id"])
        bi, li, si = map(int, original.groups())
        text = raw_text.get((pn, bi, li, si), block["source_text"])
        if previous is not None and previous < si:
            skipped = [
                raw_text.get((pn, bi, li, index), "")
                for index in range(previous + 1, si)
            ]
            # Extraction intentionally omits whitespace-only blocks. Recover
            # their exact separators without inventing spaces at style splits.
            if skipped and all(not part.strip() for part in skipped):
                text = "".join(skipped) + text
        return text

    by_block = defaultdict(list)
    untouched = []
    for block in blocks:
        match = re.search(r"-b(\d+)-l(\d+)-s(\d+)$", block["id"])
        if not match or block["style"].get("vertical"):
            untouched.append(block)
            continue
        bi, li, si = map(int, match.groups())
        by_block[(block["page_number"], bi)].append((li, si, block))
    grouped = []
    source_chunks = []
    for (pn, bi), items in by_block.items():
        lines = defaultdict(list)
        for li, si, b in items:
            cell = tuple(b["style"].get("table_cell_bbox", []))
            lines[(li, cell)].append((si, b))
        segments = []
        for (li, cell), spans in sorted(lines.items()):
            current = []
            for si, b in sorted(spans, key=lambda x: x[1]["bbox"][0]):
                if current and b["bbox"][0] - current[-1][1]["bbox"][2] > max(
                    15, b["style"]["size"] * 2
                ):
                    segments.append((li, cell, current))
                    current = []
                current.append((si, b))
            if current:
                segments.append((li, cell, current))
        chunks = []
        for li, cell, spans in segments:
            box = fitz.Rect(spans[0][1]["bbox"])
            for _, b in spans[1:]:
                box |= fitz.Rect(b["bbox"])
            size = median(b["style"]["size"] for _, b in spans)
            text = "".join(
                span_text(pn, bi, li, si, b, spans[index - 1][0] if index else None)
                for index, (si, b) in enumerate(spans)
            )
            item = dict(
                line=li, cell=cell, spans=spans, bbox=list(box), size=size, text=text
            )
            prev = chunks[-1][-1] if chunks else None
            same_paragraph = (
                prev
                and li == prev["line"] + 1
                and cell == prev["cell"]
                and abs(size - prev["size"]) < 0.5
                and abs(box.x0 - prev["bbox"][0]) < max(8, size)
                and box.y0 > prev["bbox"][1] + size * 0.6
                and -size * 0.35 <= box.y0 - prev["bbox"][3] <= size * 0.8
            )
            # PDF blocks often mix headings, table labels, and bullet rows.
            same_paragraph = (
                same_paragraph
                and not re.match(r"^\s*(?:[●•▪*-]|\d+[.)])", text)
                and not any(
                    b["type"] in {"heading", "header", "footer", "caption"}
                    for _, b in spans
                )
            )
            if same_paragraph:
                chunks[-1].append(item)
            else:
                chunks.append([item])
        for gi, chunk in enumerate(chunks):
            source_chunks.append({"pn": pn, "bi": bi, "gi": gi, "lines": chunk})
    for planned in plan_chunks(source_chunks, pages, blocks):
        pn, bi, gi, chunk = (
            planned["pn"],
            planned["bi"],
            planned["gi"],
            planned["lines"],
        )
        members = [b for line in chunk for _, b in line["spans"]]
        if (
            len(members) < min_spans
            and (len(chunk) < 2 or chunk_role(planned) not in {"prose", "list"})
            and not any(b["style"].get("table_cell") for b in members)
        ):
            untouched.extend(members)
            continue
        weights = Counter()
        for b in members:
            weights[style_key(b["style"])] += max(1, len(b["source_text"]))
        base_key = weights.most_common(1)[0][0]
        base = next(b["style"] for b in members if style_key(b["style"]) == base_key)
        definitions = {}
        runs = []
        for line_index, line in enumerate(chunk):
            for span_index, (si, b) in enumerate(line["spans"]):
                key = style_key(b["style"])
                sid = next(
                    (sid for sid, st in definitions.items() if style_key(st) == key),
                    None,
                )
                if sid is None:
                    sid = f"f{len(definitions)}"
                    definitions[sid] = {
                        k: b["style"][k]
                        for k in (
                            "font",
                            "size",
                            "color",
                            "bold",
                            "italic",
                            "superscript",
                            "underline",
                        )
                    }
                previous = line["spans"][span_index - 1][0] if span_index else None
                text = span_text(pn, bi, line["line"], si, b, previous)
                if runs and runs[-1]["style_id"] == sid:
                    runs[-1]["text"] += text
                    runs[-1]["source_ids"].append(b["id"])
                else:
                    runs.append(
                        dict(
                            id=f"s{len(runs)}",
                            text=text,
                            style_id=sid,
                            source_ids=[b["id"]],
                        )
                    )
            if line_index < len(chunk) - 1:
                # Source line breaks are layout artifacts within a paragraph.
                runs[-1]["text"] = runs[-1]["text"].rstrip() + " "
        base_id = next(
            sid for sid, st in definitions.items() if style_key(st) == base_key
        )
        box = fitz.Rect(members[0]["bbox"])
        for b in members[1:]:
            box |= fitz.Rect(b["bbox"])
        ir = dict(
            version=1,
            base_style_id=base_id,
            styles=definitions,
            runs=runs,
            source_spans=[
                dict(
                    id=b["id"],
                    bbox=b["bbox"],
                    text=b["source_text"],
                    style={
                        k: b["style"][k]
                        for k in (
                            "font",
                            "size",
                            "color",
                            "bold",
                            "italic",
                            "superscript",
                            "underline",
                        )
                    },
                )
                for b in members
            ],
        )
        first = members[0]
        grouped.append(
            {
                **first,
                "id": re.sub(r"-b\d+-l\d+-s\d+$", "", first["id"]) + f"-b{bi}-g{gi}",
                "bbox": list(box),
                "source_text": "".join(r["text"] for r in runs).strip(),
                "style": {
                    **base,
                    "digital_span": False,
                    "fixed_box": True,
                    "align": source_alignment(chunk, base, pages[pn - 1]),
                    "source_regions": [
                        region
                        for b in members
                        for region in b["style"].get("source_regions", [b["bbox"]])
                    ],
                    "underline_paths": [
                        path
                        for b in members
                        for path in b["style"].get("underline_paths", [])
                    ],
                    "rich_text": ir,
                },
            }
        )
        annotate_policy(grouped[-1], planned)
    result = untouched + grouped
    expected = [b["id"] for b in blocks]
    actual = [
        identity
        for b in result
        for identity in (
            [s["id"] for s in b["style"]["rich_text"]["source_spans"]]
            if b["style"].get("rich_text")
            else [b["id"]]
        )
    ]
    if Counter(actual) != Counter(expected) or len(actual) != len(set(actual)):
        raise StudioError(
            "layout_coverage_invalid",
            "Layout grouping lost or duplicated a source span",
            500,
        )
    allocate_flow_space(grouped, result, pages)
    # A PDF glyph box is smaller than its allocated table-cell line area.
    # Use available cell space, bounded by neighboring text and cell borders.
    source_boxes = {b["id"]: fitz.Rect(b["bbox"]) for b in result}
    for b in grouped:
        cell = b["style"].get("table_cell_bbox")
        if not cell:
            continue
        container = fitz.Rect(cell)
        original = source_boxes[b["id"]]
        candidate = fitz.Rect(original)
        if b["style"].get("align") == "center":
            candidate.x0 = container.x0 + 1
            candidate.x1 = container.x1 - 1
        else:
            candidate.x1 = max(original.x1, container.x1 - 1)
        candidate.y0 = max(container.y0 + 0.5, original.y0 - 1)
        candidate.y1 = min(container.y1 - 0.5, original.y1 + b["style"]["size"] * 1.4)
        neighbors = [
            source_boxes[o["id"]]
            for o in result
            if o["id"] != b["id"] and o["page_number"] == b["page_number"]
        ]
        for other in neighbors:
            if other.x0 < candidate.x1 and other.x1 > candidate.x0:
                if other.y0 >= original.y1 - 0.1:
                    candidate.y1 = min(
                        candidate.y1, (original.y1 + other.y0) / 2 - 0.25
                    )
                if other.y1 <= original.y0 + 0.1:
                    candidate.y0 = max(
                        candidate.y0, (original.y0 + other.y1) / 2 + 0.25
                    )
            if (
                other.y0 < original.y1
                and other.y1 > original.y0
                and other.x0 >= original.x1 - 0.1
            ):
                candidate.x1 = min(candidate.x1, other.x0 - 1)
        if candidate.contains(original) and all(
            (candidate & other).get_area() <= max(1, (original & other).get_area())
            for other in neighbors
        ):
            b["bbox"] = list(candidate)
            b["style"]["layout_geometry"] = "table_cell_space"
    for pn in range(1, len(pages) + 1):
        page = sorted(
            (b for b in result if b["page_number"] == pn),
            key=lambda b: (b["reading_order"], b["bbox"][1], b["bbox"][0]),
        )
        for order, b in enumerate(page):
            b["reading_order"] = order
    return result


def source_runs(block):
    ir = rich(block)
    return [{**r, "style": ir["styles"][r["style_id"]]} for r in ir["runs"]]


def literal_source(block):
    """Separate superscript notes from adjacent words for literal protection.

    GRE + superscript 1 is a reference marker, not the identifier GRE1.
    The original text and source geometry are unchanged in the IR.
    """
    ir = rich(block)
    if not ir:
        return block.source_text
    return "".join(
        (" " + r["text"] + " ")
        if ir["styles"][r["style_id"]].get("superscript")
        else r["text"]
        for r in ir["runs"]
    )


def literal_target(runs):
    """Apply the same reference-marker boundaries to translated rich text."""
    return "".join(
        (" " + r["text"] + " ") if r["style"].get("superscript") else r["text"]
        for r in runs
    )


def encode_groups(blocks, nonce):
    ids, parts = [], []
    for i, block in enumerate(blocks):
        pid = f"{nonce}_{i:03}"
        ids.append(pid)
        ir = rich(block)
        text = []
        for run in ir["runs"]:
            escaped = html.escape(run["text"], quote=False)
            if run["style_id"] != ir["base_style_id"]:
                escaped = f'<span id="{run["id"]}">{escaped}</span>'
            text.append(escaped)
        parts.append(f'<p id="{pid}">' + "".join(text) + "</p>")
    return ids, "\n".join(parts)


def validate_groups(output, ids, blocks):
    from app.agents.translator import LITERALS, normalize_digits, validate_literals

    try:
        if not isinstance(output, str) or "<!" in output or "<?" in output:
            raise ValueError("XML declaration")
        root = ET.fromstring("<batch>" + output + "</batch>")
        if (root.text or "").strip() or len(root) != len(ids):
            raise ValueError("paragraph count")
        expected = dict(zip(ids, blocks))
        result = {}
        for p in root:
            pid = p.get("id")
            if (
                p.tag != "p"
                or p.attrib != {"id": pid}
                or pid not in expected
                or pid in result
                or (p.tail or "").strip()
            ):
                raise ValueError("paragraph identity")
            block = expected[pid]
            ir = rich(block)
            styled = {
                r["id"]: r for r in ir["runs"] if r["style_id"] != ir["base_style_id"]
            }
            seen = Counter()
            output_runs = []
            contents = defaultdict(list)

            def add_plain(text):
                if text:
                    output_runs.append(
                        dict(
                            text=normalize_digits(text),
                            style_id=ir["base_style_id"],
                            style=ir["styles"][ir["base_style_id"]],
                        )
                    )

            add_plain(p.text)
            for span in p:
                sid = span.get("id")
                if (
                    span.tag != "span"
                    or span.attrib != {"id": sid}
                    or sid not in styled
                    or len(span)
                    or not (span.text or "").strip()
                ):
                    raise ValueError("inline identity/content")
                seen[sid] += 1
                # Discontinuous translated phrases may reuse a formatting code,
                # but protected literal codes must not be duplicated.
                if seen[sid] > 8 or (
                    seen[sid] > 1 and LITERALS.search(styled[sid]["text"])
                ):
                    raise ValueError("invalid code copy")
                text = normalize_digits(span.text)
                contents[sid].append(text)
                style_id = styled[sid]["style_id"]
                output_runs.append(
                    dict(
                        id=sid,
                        text=text,
                        style_id=style_id,
                        style=ir["styles"][style_id],
                    )
                )
                add_plain(span.tail)
            if set(seen) != set(styled):
                raise ValueError("missing inline code")
            for sid, original in styled.items():
                validate_literals(original["text"], " ".join(contents[sid]))
            text = "".join(r["text"] for r in output_runs)
            if not text.strip():
                raise ValueError("empty paragraph")
            validate_literals(literal_source(block), literal_target(output_runs))
            result[pid] = (text, output_runs)
        return [result[pid] for pid in ids]
    except (ET.ParseError, ValueError, TypeError, StudioError) as exc:
        raise StudioError(
            "invalid_rich_translation",
            "Translation changed paragraph codes, formatting references, or protected values",
            502,
        ) from exc


def export_xliff(entries, language):
    """Export standard source/target inline codes and original style definitions.

    Page geometry and original source spans remain in structure.json.
    """
    ET.register_namespace("", XLIFF_NS)

    def node(parent, name, attrs=None, text=None):
        e = ET.SubElement(parent, "{" + XLIFF_NS + "}" + name, attrs or {})
        e.text = text
        return e

    root = ET.Element(
        "{" + XLIFF_NS + "}xliff", version="2.2", srcLang="und", trgLang=language
    )
    file = node(root, "file", {"id": "f1"})
    for block, translation in entries:
        ir = rich(block)
        if not ir:
            continue
        unit = node(file, "unit", {"id": block.id})
        styled = {
            r["id"]: r for r in ir["runs"] if r["style_id"] != ir["base_style_id"]
        }
        original = node(unit, "originalData") if styled else None
        for sid, r in styled.items():
            node(
                original,
                "data",
                {"id": sid + "_start"},
                json.dumps(ir["styles"][r["style_id"]], ensure_ascii=False),
            )
            node(original, "data", {"id": sid + "_end"}, "end-style")
        segment = node(unit, "segment", {"id": "seg1"})
        source = node(
            segment,
            "source",
            {"{http://www.w3.org/XML/1998/namespace}space": "preserve"},
        )

        def append(parent, text):
            if len(parent):
                parent[-1].tail = (parent[-1].tail or "") + text
            else:
                parent.text = (parent.text or "") + text

        def code(parent, sid, text, copy_index=0):
            attrs = {
                "id": sid if not copy_index else f"{sid}_copy{copy_index}",
                "dataRefStart": sid + "_start",
                "dataRefEnd": sid + "_end",
                "type": "fmt",
                "canDelete": "no",
            }
            style = ir["styles"][styled[sid]["style_id"]]
            if style.get("bold"):
                attrs["subType"] = "xlf:b"
            elif style.get("italic"):
                attrs["subType"] = "xlf:i"
            from app.agents.translator import LITERALS

            attrs["canCopy"] = "no" if LITERALS.search(styled[sid]["text"]) else "yes"
            node(parent, "pc", attrs, text)

        for r in ir["runs"]:
            if r["id"] in styled:
                code(source, r["id"], r["text"])
            else:
                append(source, r["text"])
        target = node(
            segment,
            "target",
            {"{http://www.w3.org/XML/1998/namespace}space": "preserve"},
        )
        runs = translation.metadata_json.get("rich_text", {}).get("runs")
        if not runs:
            target.text = translation.text
        else:
            seen = Counter()
            for r in runs:
                sid = r.get("id")
                if sid in styled:
                    code(target, sid, r["text"], seen[sid])
                    seen[sid] += 1
                else:
                    append(target, r["text"])
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
