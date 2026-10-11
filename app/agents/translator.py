import re
import hashlib
import unicodedata
from collections import Counter
from app.config import StudioError

LITERALS = re.compile(r"https?://[^\s]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b[A-Z][A-Z0-9_-]*\d[A-Z0-9_-]*\b|\b\d+(?:[.,:/-]\d+)*(?:%)?")


def source_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_digits(text):
    return "".join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in text)


def validate_literals(source, translated):
    translated = normalize_digits(translated)
    expected = Counter(LITERALS.findall(source))
    actual = Counter(LITERALS.findall(translated))
    if any(actual[value] < count for value,count in expected.items()):
        raise StudioError("protected_value_changed","Translation changed a protected source value")
    return translated


def protect(text):
    literals = []
    def replace(match):
        literals.append(match.group())
        return f"ZXQKEEP{len(literals)-1}QXZ"
    return LITERALS.sub(replace, text), literals


def restore(text, literals):
    for i, literal in enumerate(literals):
        token = f"ZXQKEEP{i}QXZ"
        if text.count(token) != 1:
            raise StudioError("protected_value_changed", "Translation altered a protected placeholder")
        text = text.replace(token, literal)
    return text


class TranslatorAgent:
    def __init__(self, provider):
        self.provider = provider

    async def run(self, block, language, script):
        provenance = {"source_hash":source_hash(block.source_text)}
        if not LITERALS.sub("",block.source_text).strip(" \n\t.,;:/()%+-|"):
            return block.source_text,{**provenance,"unchanged_literal":True,"usage":{},"protected_values":LITERALS.findall(block.source_text)}
        protected, literals = protect(block.source_text)
        body = await self.provider.translate(protected, language, script)
        if body.get("truncated"):
            raise StudioError("translation_truncated", "Translation was cut off; split or review the block")
        try:
            translated = restore(body["translation"],literals)
        except StudioError:
            retry = await self.provider.translate(block.source_text,language,script)
            if retry.get("truncated"):
                raise StudioError("translation_truncated","Translation was cut off")
            try:
                translated = validate_literals(block.source_text,retry["translation"])
            except StudioError:
                # Final bounded fallback: translate prose runs while copying literals
                # verbatim. This trades some context for guaranteed identifier integrity.
                pieces=[]
                position=0
                for match in list(LITERALS.finditer(block.source_text))+[None]:
                    end=match.start() if match else len(block.source_text)
                    prose=block.source_text[position:end]
                    if prose.strip() and re.search(r"\w",prose):
                        part=await self.provider.translate(prose.strip(),language,script)
                        if part.get("truncated"):
                            raise StudioError("translation_truncated","Literal-preserving translation was cut off")
                        leading=prose[:len(prose)-len(prose.lstrip())]
                        trailing=prose[len(prose.rstrip()):]
                        pieces.append(leading+part["translation"].strip()+trailing)
                        for key,value in part.get("usage",{}).items():
                            retry.setdefault("usage",{})[key]=retry.get("usage",{}).get(key,0)+value
                    else:
                        pieces.append(prose)
                    if match:
                        pieces.append(match.group())
                        position=match.end()
                translated=validate_literals(block.source_text,"".join(pieces))
            body = {"usage":{key:body.get("usage",{}).get(key,0)+retry.get("usage",{}).get(key,0) for key in {**body.get("usage",{}),**retry.get("usage",{})}}}
        return translated, {**provenance,"usage": body.get("usage", {}), "protected_values": literals}
