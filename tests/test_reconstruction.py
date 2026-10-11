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
