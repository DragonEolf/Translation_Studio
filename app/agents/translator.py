import re
import hashlib
import unicodedata
import html
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from app.config import StudioError

LITERALS = re.compile(r"https?://[^\s]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b[A-Z][A-Z0-9_-]*\d[A-Z0-9_-]*\b|\b\d+(?:[.,:/-]\d+)*(?:%)?")


def source_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def literal_only(text):
    return not LITERALS.sub("",text).strip(" \n\t.,;:/()%+-|")


def encode_batch(blocks, nonce):
    ids = [f"{nonce}_{i:03}" for i in range(len(blocks))]
    text = "\n".join(f'<span id="{identity}">{html.escape(b.source_text,quote=False)}</span>' for identity,b in zip(ids,blocks))
    return ids,text


def validate_batch(output, ids, blocks):
    """Require exact ordered IDs and text-only XML; decode entities once."""
    try:
        if not isinstance(output,str) or "<!" in output or "<?" in output:
            raise ValueError()
        root = ET.fromstring("<batch>"+output+"</batch>")
        if (root.text or "").strip() or len(root)!=len(ids):
            raise ValueError()
        texts=[]
        for child,identity,block in zip(root,ids,blocks):
            if child.tag!="span" or child.attrib!={"id":identity} or len(child) or (child.tail or "").strip() or not (child.text or "").strip():
                raise ValueError()
            texts.append(validate_literals(block.source_text,child.text.strip()))
        return texts
    except (ET.ParseError,ValueError,TypeError,StudioError) as exc:
        raise StudioError("invalid_translation_batch","Translation batch has invalid tags, IDs, content, or protected values",502) from exc


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

    def batches(self, blocks, size=30, max_chars=6000):
        batch=[]
        for block in blocks:
            # Count escaped XML, not just source text. The fixed-length nonce
            # has the same length as the live one used by translate_batch.
            candidate=batch+[block]
            if batch and (block.page_number!=batch[0].page_number or len(candidate)>size or len(encode_batch(candidate,"B"+"0"*12)[1])>max_chars):
                yield batch
                batch=[]
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
        if len(blocks)==1:
            stats["individual_calls"]=stats.get("individual_calls",0)+1
            block=blocks[0]
            try:
                text,metadata=await self.run(block,language,script)
                yield block,text,metadata,None
            except StudioError as exc:
                if exc.code not in {"translation_truncated","protected_value_changed"}:
                    raise
                yield block,None,None,exc
            return
        nonce="B"+uuid.uuid4().hex[:12]
        ids,payload=encode_batch(blocks,nonce)
        stats["batch_calls"]=stats.get("batch_calls",0)+1
        try:
            body=await self.provider.translate(payload,language,script)
        except StudioError as exc:
            if exc.code not in {"translation_truncated","protected_value_changed"}:
                raise
            body={"truncated":True}
        usage=body.get("usage",{})
        stats["batch_tokens"]=stats.get("batch_tokens",0)+usage.get("total_tokens",0)
        try:
            if body.get("truncated"):
                raise StudioError("invalid_translation_batch","Translation batch was truncated",502)
            texts=validate_batch(body.get("translation"),ids,blocks)
        except StudioError:
            stats["invalid_batches"]=stats.get("invalid_batches",0)+1
            middle=len(blocks)//2
            for half in (blocks[:middle],blocks[middle:]):
                async for result in self.translate_batch(half,language,script,stats):
                    yield result
            return
        # Store each API usage once, rather than multiplying it by span count.
        for index,(block,text) in enumerate(zip(blocks,texts)):
            yield block,text,{"source_hash":source_hash(block.source_text),"protected_values":LITERALS.findall(block.source_text),"usage":usage if index==0 else {},"batch_id":nonce,"batch_size":len(blocks)},None

    async def run(self, block, language, script):
        provenance = {"source_hash":source_hash(block.source_text)}
        if literal_only(block.source_text):
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
