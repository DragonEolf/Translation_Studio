from pathlib import Path
from types import SimpleNamespace
import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from app.agents.block_maker import BlockMakerAgent
from app.agents.text_reader import TextReaderAgent
from app.agents.translator import TranslatorAgent,source_hash
from app.services.text_fitter import FontRegistry
from app.config import Settings
from app.main import create_app
from test_studio import wait


def test_merged_cells_are_renderable_and_styles_do_not_leak(tmp_path):
    path=tmp_path/"merged.pdf"
    with fitz.open() as pdf:
        page=pdf.new_page(width=400,height=300)
        page.draw_rect(fitz.Rect(50,50,350,80),fill=(0,0,.5))
        for y in [50,80,120,160]:
            page.draw_line((50,y),(350,y))
        for x in [50,350]:
            page.draw_line((x,50),(x,160))
        page.draw_line((200,80),(200,160))
        page.insert_text((100,70),"Merged heading",fontsize=12,color=(1,1,1))
        for x,y,text in [(60,100,"Name"),(210,100,"Count"),(60,140,"Items"),(210,140,"12")]:
            page.insert_text((x,y),text,fontsize=10)
        page.insert_text((50,40),"Outside title",fontsize=12)
        pdf.save(path)
    blocks=BlockMakerAgent().run("test",TextReaderAgent().run(path))
    assert len(blocks)==6
    assert len([b for b in blocks if b["type"]=="table_cell"])==5
    assert not any(b["style"].get("complex_table") for b in blocks)
    assert [b["source_text"] for b in blocks if b["type"]!="table_cell"]==["Outside title"]
    assert next(b for b in blocks if b["source_text"]=="Merged heading")["style"]["color"]==0xffffff
    assert all(b["style"]["color"]==0 for b in blocks if b["source_text"] in {"Name","Count","Items","12"})


async def test_literal_fallback_and_numeric_cells():
    class DamagesLiterals:
        def __init__(self): self.calls=0
        async def translate(self,*args):
            self.calls+=1
            return {"translation":"अनुवाद","usage":{},"truncated":False}
    provider=DamagesLiterals()
    agent=TranslatorAgent(provider)
    text,meta=await agent.run(SimpleNamespace(source_text="Course DA7016 starts 26"),"hi","native")
    assert "DA7016" in text and "26" in text and "अनुवाद" in text
    assert meta["source_hash"]==source_hash("Course DA7016 starts 26")
    count=provider.calls
    text,_=await agent.run(SimpleNamespace(source_text="8.87"),"hi","native")
    assert text=="8.87" and provider.calls==count


def test_indic_bullets_and_searchable_actual_text(tmp_path):
    fonts=Path("fonts").resolve()
    if not fonts.exists(): pytest.skip("Local Noto fonts are required")
    selected=FontRegistry(fonts).resolve("hi","native")
    assert selected.endswith("-Regular.ttf") and "Condensed" not in selected
    class Hindi:
        async def translate(self,text,*args):
            return {"translation":"● "+text.replace("Hello","नमस्ते"),"truncated":False}
    with fitz.open() as pdf:
        page=pdf.new_page(width=300,height=200)
        page.insert_text((30,60),"Hello world 12",fontsize=12)
        data=pdf.tobytes()
    with TestClient(create_app(Settings(studio_data_dir=tmp_path,studio_font_dir=fonts),Hindi())) as client:
        upload=client.post("/v1/documents",files={"file":("test.pdf",data)})
        doc=upload.json()["document_id"]
        wait(client,upload)
        job=wait(client,client.post(f"/v1/documents/{doc}/translations",json={"target_language":"hi"}))
        assert job["status"]=="completed",job
        assert job["payload"]["result"]["rendered_blocks"]==1
        with fitz.open(stream=client.get(f"/v1/documents/{doc}/artifacts/final.pdf").content,filetype="pdf") as result:
            text=" ".join(result[0].get_text().split())
            assert text=="● नमस्ते world 12"
            assert "�" not in text and "Hello" not in text


def test_multiline_indic_text_has_no_duplicates(tmp_path):
    from app.services.renderer import Renderer
    from app.services.text_fitter import TextFitter
    fonts=Path("fonts").resolve()
    if not fonts.exists(): pytest.skip("Local Noto fonts are required")
    font=FontRegistry(fonts).resolve("hi","native")
    source=tmp_path/"source.pdf"
    with fitz.open() as pdf:
        page=pdf.new_page(width=300,height=250)
        pdf.save(source)
    block=SimpleNamespace(bbox=[30,30,150,220],style={"size":12,"color":0,"ocr":True})
    text="● नमस्ते संसार 12\n● यह परीक्षण है और सभी शब्द सुरक्षित हैं 34"
    layout=SimpleNamespace(**TextFitter().fit(block,text,300,250,[],font))
    assert layout.fit_status=="fitted"
    destination=tmp_path/"translated.pdf"
    Renderer().render_page(source,1,[(block,SimpleNamespace(text=text),layout)],destination)
    with fitz.open(destination) as pdf:
        assert " ".join(pdf[0].get_text().split())==" ".join(text.split())


def test_ocr_reconstruction_preserves_title_and_paragraph_breaks(tmp_path):
    from app.services.renderer import Renderer
    from app.services.text_fitter import TextFitter
    source = tmp_path / "source.pdf"
    with fitz.open() as pdf:
        pdf.new_page(width=300, height=250)
        pdf.save(source)
    text = "TITLE\n\nFirst paragraph.\n\nSecond paragraph."
    block = SimpleNamespace(bbox=[30,30,270,220], style={"size":12,"color":0,"ocr":True})
    layout = SimpleNamespace(**TextFitter().fit(block,text,300,250,[],None))
    assert layout.fit_status == "fitted"
    destination = tmp_path / "translated.pdf"
    Renderer().render_page(source,1,[(block,SimpleNamespace(text=text),layout)],destination)
    with fitz.open(destination) as pdf:
        words = {w[4]:w for w in pdf[0].get_text("words")}
        # Actual rendered positions must preserve the two explicit blank lines.
        assert words["First"][1] - words["TITLE"][1] > 20
        assert words["Second"][1] - words["First"][1] > 20


def test_ocr_signature_crop_is_retained_pixel_for_pixel(tmp_path):
    from app.agents.reorganiser import ReorganiserAgent
    from app.agents.checker import CheckerAgent
    from app.services.renderer import Renderer
    source = tmp_path / "signature.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page(width=300,height=200)
        page.insert_text((30,50),"Landlord's Signature:")
        page.draw_polyline([(140,60),(160,30),(155,65),(200,40),(220,55)])
        pdf.save(source)
    block = SimpleNamespace(id="signature",source_text="Landlord's Signature: OCR guess",bbox=[25,25,240,80],style={"size":24,"color":0,"ocr":True},confidence=.9)
    translation = SimpleNamespace(text="Translated signature",target_language="en",script="native")
    layout = SimpleNamespace(**ReorganiserAgent(FontRegistry()).run(block,translation,SimpleNamespace(width=300,height=200),[]))
    assert layout.fit_status == "signature_review_required"
    destination = tmp_path / "translated.pdf"
    entries = [(block,translation,layout)]
    Renderer().render_page(source,1,entries,destination)
    with fitz.open(source) as original, fitz.open(destination) as output:
        assert original[0].get_pixmap().samples == output[0].get_pixmap().samples
    assert [i["code"] for i in CheckerAgent().run(source,destination,1,entries)] == ["signature_review_required"]


def test_unruled_table_row_cannot_silently_reflow_into_prose(tmp_path):
    from app.agents.reorganiser import ReorganiserAgent
    from app.services.renderer import Renderer
    source = tmp_path / "table.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page(width=500,height=200)
        for x,text in [(30,"01"),(100,"CY1001"),(200,"Chemistry"),(400,"10")]:
            page.insert_text((x,60),text,fontsize=10)
        pdf.save(source)
    blocks = BlockMakerAgent().run("test",TextReaderAgent().run(source))
    assert len(blocks) == 4
    entries = []
    for b in blocks:
        translation = SimpleNamespace(text="Physics" if b["source_text"]=="Chemistry" else b["source_text"],target_language="en",script="native")
        result = ReorganiserAgent(FontRegistry()).run(SimpleNamespace(**b),translation,SimpleNamespace(width=500,height=200),[])
        assert result["fit_status"] == "fitted"
        assert result["bbox"] == b["bbox"]
        entries.append((SimpleNamespace(**b),translation,SimpleNamespace(**result)))
    destination = tmp_path / "translated.pdf"
    Renderer().render_page(source,1,entries,destination)
    with fitz.open(destination) as pdf:
        for block,translation,_ in entries:
            assert pdf[0].get_textbox(fitz.Rect(block.bbox)).strip() == translation.text
        assert "Chemistry" not in pdf[0].get_text()


def test_selectable_lines_and_styles_keep_independent_fixed_boxes(tmp_path):
    from app.services.text_fitter import TextFitter
    source = tmp_path / "styles.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page(width=400,height=300)
        page.insert_text((30,60),"Title",fontsize=18,fontname="hebo")
        page.insert_text((30,100),"First line",fontsize=12)
        page.insert_text((110,100),"Bold",fontsize=12,fontname="hebo")
        page.insert_text((30,120),"Second line",fontsize=12)
        pdf.save(source)
        expected = [(s["text"],list(s["bbox"])) for b in page.get_text("dict",sort=True)["blocks"] if b["type"]==0 for l in b["lines"] for s in l["spans"] if s["text"].strip()]
    blocks = BlockMakerAgent().run("test",TextReaderAgent().run(source))
    assert sorted((b["source_text"],b["bbox"]) for b in blocks) == sorted(expected)
    assert next(b for b in blocks if b["source_text"]=="Bold")["style"]["bold"]
    for block in blocks:
        assert block["style"]["source_regions"] == [block["bbox"]]
        result = TextFitter().fit(SimpleNamespace(**block),"Too long "*100,400,300,[],None)
        assert result["fit_status"] == "fitted"
        assert 0 < result["font_size"] < block["style"]["size"]
        assert result["bbox"] == block["bbox"]


def test_geometry_upgrade_reuses_only_exact_span_revisions(tmp_path):
    from app.models import Document,Page,Block,Translation
    from app.orchestrator import latest
    from sqlalchemy import select
    root = tmp_path / "doc"
    root.mkdir()
    with fitz.open() as pdf:
        page = pdf.new_page(width=400,height=300)
        page.insert_text((30,60),"Title",fontsize=18)
        page.insert_text((30,100),"Code",fontsize=12)
        page.insert_text((150,100),"Course",fontsize=12)
        pdf.save(root/"source.pdf")
    extracted = BlockMakerAgent().run("doc",TextReaderAgent().run(root/"source.pdf"))
    title = next(b for b in extracted if b["source_text"]=="Title")
    row = [b for b in extracted if b["source_text"]!="Title"]
    app = create_app(Settings(studio_data_dir=tmp_path,studio_font_dir=None))
    with app.state.sessions() as s:
        s.add(Document(id="doc",filename="source.pdf",sha256="test",target_language="en"))
        s.flush()
        s.add(Page(id="doc-p1",document_id="doc",number=1,width=400,height=300,metadata_json={"extraction_method":"digital"}))
        s.add(Block(**{**title,"id":"old-title"}))
        bbox = list(fitz.Rect(row[0]["bbox"])|fitz.Rect(row[1]["bbox"]))
        s.add(Block(id="old-row",document_id="doc",page_number=1,type="paragraph",reading_order=1,bbox=bbox,source_text="Code\nCourse",style={"size":12,"color":0}))
        s.flush()
        s.add(Translation(block_id="old-title",target_language="en",script="native",text="Heading",revision=2,metadata_json={"source_hash":source_hash("Title")}))
        s.add(Translation(block_id="old-row",target_language="en",script="native",text="Combined translation",revision=1,metadata_json={"source_hash":source_hash("Code\nCourse")}))
        s.commit()
    app.state.orchestrator.refresh_geometry("doc")
    app.state.orchestrator.refresh_geometry("doc")
    with app.state.sessions() as s:
        assert s.get(Block,"old-row").style["retired"]
        assert latest(s,title["id"],"en","native").text == "Heading"
        assert latest(s,title["id"],"en","native").revision == 2
        assert len(list(s.scalars(select(Translation).where(Translation.block_id==title["id"])))) == 1
        assert all(latest(s,b["id"],"en","native") is None for b in row)


def test_hindi_translation_fits_tight_span_without_moving_neighbor(tmp_path):
    from app.agents.reorganiser import ReorganiserAgent
    from app.agents.checker import CheckerAgent
    from app.services.renderer import Renderer
    fonts = Path("fonts").resolve()
    if not fonts.exists(): pytest.skip("Local Noto fonts are required")
    source = tmp_path / "tight.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page(width=300,height=200)
        page.insert_text((30,60),"Ecology and Environment",fontsize=7)
        page.insert_text((200,60),"2023",fontsize=7)
        pdf.save(source)
    b = next(b for b in BlockMakerAgent().run("test",TextReaderAgent().run(source)) if b["source_text"]=="Ecology and Environment")
    block = SimpleNamespace(**b)
    translation = SimpleNamespace(text="पारिस्थितिकी और पर्यावरण का विस्तृत अध्ययन",target_language="hi",script="native")
    layout = SimpleNamespace(**ReorganiserAgent(FontRegistry(fonts)).run(block,translation,SimpleNamespace(width=300,height=200),[]))
    assert layout.fit_status == "fitted"
    assert layout.bbox == block.bbox
    assert layout.font_size < 6
    entries = [(block,translation,layout)]
    destination = tmp_path / "translated.pdf"
    Renderer().render_page(source,1,entries,destination)
    with fitz.open(source) as original,fitz.open(destination) as rendered:
        assert original[0].search_for("2023") == rendered[0].search_for("2023")
        assert translation.text in rendered[0].get_textbox(fitz.Rect(block.bbox))
    issues = CheckerAgent().run(source,destination,1,entries)
    assert issues == []
