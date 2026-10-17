import html
from pathlib import Path
import pymupdf as fitz

SCRIPT_FONTS = {
    "hi": "Devanagari", "mr": "Devanagari", "ne": "Devanagari", "sa": "Devanagari", "mai": "Devanagari", "doi": "Devanagari", "brx": "Devanagari", "kok": "Devanagari", "ks-Deva": "Devanagari", "sd": "Devanagari",
    "bn": "Bengali", "as": "Bengali", "mni-Beng": "Bengali", "gu": "Gujarati", "pa": "Gurmukhi", "kn": "Kannada", "ml": "Malayalam", "ta": "Tamil", "te": "Telugu", "or": "Oriya", "sat": "OlChiki", "mni": "MeeteiMayek", "ur": "Arabic", "ks": "Arabic", "sd-Arab": "Arabic",
}


class FontRegistry:
    def __init__(self, directory=None):
        self.paths = {}
        if directory:
            if not Path(directory).is_dir():
                raise ValueError("STUDIO_FONT_DIR must be an existing directory")
            for path in Path(directory).rglob("*"):
                if path.suffix.lower() in {".ttf", ".otf"}:
                    fitz.Font(fontfile=str(path))  # validate actual font files
                    self.paths[path.stem.lower()] = path

    def resolve(self, language, script, bold=False, condensed=False):
        suffix = "" if language == "en" or script == "roman" else SCRIPT_FONTS[language]
        candidates = [(name,path) for name,path in self.paths.items() if "noto" in name and (suffix.lower() in name if suffix else name.startswith("notosans-"))]
        def rank(item):
            name = item[0]
            weight = "bold" if bold else "regular"
            width_match = "_condensed-" in name if condensed else "condensed" not in name
            return (not width_match,not name.endswith("-"+weight),"variablefont" in name,name)
        if candidates:
            return str(sorted(candidates,key=rank)[0][1])
        return None if not suffix else "missing"


def markup(text, size, color, font_path=None, bold=False, align="left", italic=False, preserve_breaks=False):
    rgb = f"#{int(color):06x}"
    face = "@font-face {font-family: studio; src: url('" + Path(font_path).name + "');}" if font_path else ""
    family = "studio" if font_path else "sans-serif"
    css = f"{face} * {{margin:0;padding:0}} div {{font-family:{family},sans-serif;font-size:{size}pt;color:{rgb};font-weight:{'bold' if bold else 'normal'};font-style:{'italic' if italic else 'normal'};text-align:{align};line-height:1.05;}} .symbol {{font-family:sans-serif}}"
    # Physical source line breaks are not paragraph boundaries. Keep explicit bullets.
    if not preserve_breaks:
        text = __import__("re").sub(r"\n(?!\s*[●•▪*-]\s)"," ",text)
    content = html.escape(text).replace("\n","<br/>")
    content = content.replace("●",'<span class="symbol">●</span>').replace("•",'<span class="symbol">•</span>')
    return f"<div>{content}</div>", css


class TextFitter:
    def fit(self, block, text, width, height, obstacles, font_path):
        source = fitz.Rect(block.bbox)
        original = float(block.style["size"])
        if block.style.get("fixed_box"):
            # Find the largest fitting size; zero is a search bound, never a
            # rendered font size. There is no readability floor for fixed spans.
            low, high, best = 0.0, original, None
            for attempt in range(16):
                size = original if attempt == 0 else (low+high)/2
                with fitz.open() as doc:
                    page = doc.new_page(width=width,height=height)
                    content,css = markup(text,size,block.style["color"],font_path,block.style.get("bold",False),block.style.get("align","left"),block.style.get("italic",False))
                    spare,_ = page.insert_htmlbox(source,content,css=css,archive=fitz.Archive(str(Path(font_path).parent)) if font_path else None,scale_low=1)
                if spare >= 0:
                    best = size
                    low = size
                    if attempt == 0:
                        break
                else:
                    high = size
            return {"bbox":list(source),"font_size":best if best is not None else original,"font":font_path or "sans-serif","line_breaks":text.splitlines() if best is not None else [],"fit_status":"fitted" if best is not None else "overflow"}
        else:
            minimum = min(original,max(6,original*.7)) if block.style.get("table_cell") else min(original,max(original*.7,8))
        expanded = fitz.Rect(source)
        # Expand downward only when the entire new band is free of text/images/vectors.
        bottom = source.y1 if block.style.get("fixed_box") or block.style.get("table_cell") or block.style.get("ocr") else min(height - 12, source.y1 + max(20, source.height))
        for obstacle in obstacles:
            other = fitz.Rect(obstacle)
            if other.y0 >= source.y1 - .1 and other.x0 < source.x1 and other.x1 > source.x0:
                bottom = min(bottom, other.y0 - 2)
        expanded.y1 = max(source.y1, bottom)
        candidates = [(source, original), (expanded, original)]
        size = original - .5
        while size >= minimum:
            candidates.append((expanded, size))
            size -= .5
        candidates.append((expanded, minimum))
        for rect, size in candidates:
            doc = fitz.open()
            page = doc.new_page(width=width, height=height)
            content, css = markup(text, size, block.style["color"], font_path, block.style.get("bold", False),block.style.get("align","left"),block.style.get("italic",False),preserve_breaks=block.style.get("ocr",False))
            spare, scale = page.insert_htmlbox(rect, content, css=css, archive=fitz.Archive(str(Path(font_path).parent)) if font_path else None, scale_low=1)
            doc.close()
            if spare >= 0:
                return {"bbox": list(rect), "font_size": size, "font": font_path or "sans-serif", "line_breaks": text.splitlines(), "fit_status": "fitted"}
        return {"bbox": list(source), "font_size": original, "font": font_path or "sans-serif", "line_breaks": [], "fit_status": "overflow"}
