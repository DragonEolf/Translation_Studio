"""Convert supported OCR math notation without altering currency or source data."""
import re


SYMBOLS = {
    'rightarrow':'→', 'to':'→', 'longrightarrow':'⟶',
    'leftarrow':'←', 'gets':'←', 'longleftarrow':'⟵',
    'leftrightarrow':'↔', 'Rightarrow':'⇒', 'Leftarrow':'⇐',
    'Leftrightarrow':'⇔', 'Longrightarrow':'⟹', 'mapsto':'↦',
    'uparrow':'↑', 'downarrow':'↓', 'times':'×', 'pm':'±',
    'leq':'≤', 'geq':'≥', 'neq':'≠', 'approx':'≈',
    'alpha':'α', 'beta':'β', 'gamma':'γ', 'delta':'δ',
    'theta':'θ', 'lambda':'λ', 'mu':'μ', 'pi':'π',
    'sigma':'σ', 'phi':'φ', 'omega':'ω', 'xi':'ξ',
}
MATH = re.compile(r'(?<![\\$])(?P<delimiter>\${1,2})(?!\$)(?P<body>[^$]*?)(?<!\\)(?P=delimiter)(?!\$)')
COMMAND = re.compile(r'\\([A-Za-z]+)')


def display_text(block, text):
    if not block.style.get('ocr'):
        return text

    def replace(match):
        body = match['body']
        commands = COMMAND.findall(body)
        # Balanced display-math delimiters can also wrap plain numeric labels.
        if match['delimiter'] == '$$' and re.fullmatch(r'\s*(?:[0-9]+|\(\s*[0-9]+\s*\))\s*', body):
            return body.strip()
        # Leave unsupported mathematics intact and report it for review. Never
        # treat ordinary $100, escaped dollars, or code as disposable delimiters.
        if not commands or any(command not in SYMBOLS for command in commands):
            return match[0]
        converted = COMMAND.sub(lambda command: SYMBOLS[command[1]], body)
        if re.search(r'[\\{}^_]', converted):
            return match[0]
        return converted.strip()

    return MATH.sub(replace, text)


def has_unrendered_notation(block, text):
    if not block.style.get('ocr'):
        return False
    text = display_text(block, text)
    return bool(COMMAND.search(text) or '$$' in text or re.search(r'(?<!\\)\$(?!\s*\d)', text))
