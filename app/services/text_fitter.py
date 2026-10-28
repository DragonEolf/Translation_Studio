import html
import hashlib
from functools import lru_cache
from pathlib import Path
import pymupdf as fitz

SCRIPT_FONTS = {
    "hi": "Devanagari", "mr": "Devanagari", "ne": "Devanagari", "sa": "Devanagari", "mai": "Devanagari", "doi": "Devanagari", "brx": "Devanagari", "kok": "Devanagari", "ks-Deva": "Devanagari", "sd": "Devanagari",
    "bn": "Bengali", "as": "Bengali", "mni-Beng": "Bengali", "gu": "Gujarati", "pa": "Gurmukhi", "kn": "Kannada", "ml": "Malayalam", "ta": "Tamil", "te": "Telugu", "or": "Oriya", "sat": "OlChiki", "mni": "MeeteiMayek", "ur": "Arabic", "ks": "Arabic", "sd-Arab": "Arabic",
}


RTL_LANGUAGES = frozenset({"ur", "ks", "sd-Arab"})


def text_direction(language, script="native"):
    """Use the target script, rather than the source text, for paragraph direction."""
    return "rtl" if language in RTL_LANGUAGES and script != "roman" else "ltr"


def text_alignment(align, direction):
    # MuPDF Story mirrors left/right alignment under dir=rtl: CSS left is
    # the physical right edge. Preserve centred / justified paragraphs.
    return "left" if direction == "rtl" and align in {"left", "right", "start"} else align


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

    @lru_cache(maxsize=512)
    def glyph_fallbacks(self, text, primary, bold=False):
        """Only missing characters get a verified installed fallback font."""
        chosen = loaded_font(primary)
        missing = {c for c in text if not c.isspace() and c not in "\u200c\u200d"
                   and not chosen.has_glyph(ord(c))}
        if not missing:
            return {}
        candidates = sorted(self.paths.values(), key=lambda p: (
            not p.stem.lower().startswith("notosans-"),
            not p.stem.lower().endswith("-bold" if bold else "-regular"), str(p)))
        result = {}
        for char in sorted(missing):
            for path in candidates:
                if loaded_font(str(path)).has_glyph(ord(char)):
                    result[char] = str(path)
                    break
        return result

    def supports(self, text, primary, bold=False):
        if primary == "missing":
            return False
        replacements = self.glyph_fallbacks(text, primary, bold)
        legacy = [loaded_font(None), loaded_font("zapfdingbats"), loaded_font("symb")]
        chosen = loaded_font(primary)
        return all(c.isspace() or c in "\u200c\u200d" or chosen.has_glyph(ord(c))
                   or c in replacements or any(f.has_glyph(ord(c)) for f in legacy)
                   for c in text)

    def resolve(self, language, script, bold=False, condensed=False, width=None):
        suffix = "" if language == "en" or script == "roman" else SCRIPT_FONTS[language]
        candidates = [(name,path) for name,path in self.paths.items() if "noto" in name and (suffix.lower() in name if suffix else name.startswith("notosans-"))]
        if width is not None:
            candidates = [(name,path) for name,path in candidates if font_width(name) == width]
        def rank(item):
            name = item[0]
            weight = "bold" if bold else "regular"
            width_match = "_condensed-" in name if condensed else "condensed" not in name
            return (not width_match,not name.endswith("-"+weight),"variablefont" in name,name)
        if candidates:
            return str(sorted(candidates,key=rank)[0][1])
        if width not in {None, 'normal'}:
            return 'missing'
        return None if not suffix else "missing"


@lru_cache(maxsize=128)
def loaded_font(path):
    if path in {None, "sans-serif", "helv", "zapfdingbats", "symb"}:
        return fitz.Font(path if path not in {None, "sans-serif"} else "helv")
    return fitz.Font(fontfile=str(path))


def fallback_markup(text, primary, bold, registry):
    """Keep supported runs intact, so joining and bidi shaping remain intact."""
    replacements = registry.glyph_fallbacks(text, primary, bold) if registry else {}
    if not replacements:
        return html.escape(text), ""
    paths = sorted(set(replacements.values()))
    names = {p: "fallback" + hashlib.sha256(p.encode()).hexdigest()[:12] for p in paths}
    faces = "".join("@font-face {font-family:" + names[p] + ";src:url('" +
                    Path(p).as_posix() + "');}" for p in paths)
    parts = []
    start = 0
    for i, char in enumerate(text):
        if char not in replacements:
            continue
        parts.append(html.escape(text[start:i]))
        parts.append('<span style="font-family:' + names[replacements[char]] +
                     ';font-weight:normal">' + html.escape(char) + '</span>')
        start = i + 1
    parts.append(html.escape(text[start:]))
    return ''.join(parts), faces


def font_width(path):
    name = str(path).lower()
    for width in ('extra_condensed', 'semi_condensed', 'condensed'):
        if width.replace('_','') in name.replace('_','').replace('-',''):
            return width
    return 'normal'


def markup(text, size, color, font_path=None, bold=False, align="left", italic=False, preserve_breaks=False, underline=False, direction="ltr", registry=None):
    align = text_alignment(align, direction)
    rgb = f"#{int(color):06x}"
    face = "@font-face {font-family: studio; src: url('" + Path(font_path).name + "');}" if font_path else ""
    family = "studio" if font_path else "sans-serif"
    css = f"{face} * {{margin:0;padding:0}} div {{font-family:{family},sans-serif;font-size:{size}pt;color:{rgb};font-weight:{'bold' if bold else 'normal'};font-style:{'italic' if italic else 'normal'};text-align:{align};direction:{direction};text-decoration:{'underline' if underline else 'none'};line-height:1.05;}} .symbol {{font-family:sans-serif}}"
    # Physical source line breaks are not paragraph boundaries. Keep explicit bullets.
    if not preserve_breaks:
        text = __import__("re").sub(r"\n(?!\s*[●•▪*-]\s)"," ",text)
    content, fallback_faces = fallback_markup(text, font_path, bold, registry)
    css += fallback_faces
    content = content.replace("\n","<br/>")
    content = content.replace("●",'<span class="symbol">●</span>').replace("•",'<span class="symbol">•</span>')
    return f'<div dir="{direction}">{content}</div>', css


def rich_markup(runs, size, original_size, fonts, align="left", direction="ltr", registry=None):
    """Render local style references using actual Regular/Bold font files."""
    align = text_alignment(align, direction)
    faces=[]
    for bold,path in fonts.items():
        if path:
            faces.append("@font-face {font-family: studio"+str(int(bold))+"; src: url('"+Path(path).as_posix()+"');}")
    parts=[]
    for run in runs:
        style=run['style']; bold=bool(style.get('bold'))
        family='studio'+str(int(bold)) if fonts.get(bold) else 'sans-serif'
        ratio=float(style.get('size',original_size))/original_size
        inline=f"font-family:{family};font-size:{size*ratio}pt;color:#{int(style.get('color',0)):06x};font-weight:{'normal' if fonts.get(bold) else 'bold' if bold else 'normal'};font-style:{'italic' if style.get('italic') else 'normal'}"
        if style.get('underline'):
            inline+=';text-decoration:underline'
        if style.get('superscript'):
            inline+=';vertical-align:super'
        text, fallback_faces = fallback_markup(run['text'], fonts.get(bold), bold, registry)
        faces.append(fallback_faces)
        text=text.replace('●','<span class="symbol">●</span>').replace('•','<span class="symbol">•</span>')
        parts.append('<span style="'+inline+'">'+text+'</span>')
    css=''.join(faces)+f'* {{margin:0;padding:0}} div {{font-size:{size}pt;line-height:1.05;text-align:{align};direction:{direction}}} .symbol {{font-family:sans-serif}}'
    return '<div dir="'+direction+'">'+''.join(parts)+'</div>',css


class TextFitter:
    def __init__(self, fonts=None):
        self.fonts = fonts

    def fit(self, block, text, width, height, obstacles, font_path, rich_runs=None, rich_fonts=None, direction="ltr"):
        source = fitz.Rect(block.bbox)
        original = float(block.style["size"])
        def content_at(size):
            if rich_runs is not None:
                return rich_markup(rich_runs,size,original,rich_fonts or {},block.style.get("align","left"),direction=direction,registry=self.fonts)
            return markup(text,size,block.style["color"],font_path,block.style.get("bold",False),block.style.get("align","left"),block.style.get("italic",False),preserve_breaks=block.style.get("ocr",False),underline=block.style.get("underline",False),direction=direction,registry=self.fonts)
        if block.style.get("fixed_box"):
            # Find the largest fitting size; zero is a search bound, never a
            # rendered font size. There is no readability floor for fixed spans.
            low, high, best = 0.0, original, None
            for attempt in range(16):
                size = original if attempt == 0 else (low+high)/2
                with fitz.open() as doc:
                    page = doc.new_page(width=width,height=height)
                    content,css = content_at(size)
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
            content, css = content_at(size)
            spare, scale = page.insert_htmlbox(rect, content, css=css, archive=fitz.Archive(str(Path(font_path).parent)) if font_path else None, scale_low=1)
            doc.close()
            if spare >= 0:
                return {"bbox": list(rect), "font_size": size, "font": font_path or "sans-serif", "line_breaks": text.splitlines(), "fit_status": "fitted"}
        return {"bbox": list(source), "font_size": original, "font": font_path or "sans-serif", "line_breaks": [], "fit_status": "overflow"}
