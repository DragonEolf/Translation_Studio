from types import SimpleNamespace as N
import pymupdf as fitz
from app.agents.text_reader import TextReaderAgent
from app.agents.block_maker import BlockMakerAgent
from app.services.rich_text import group_blocks
from app.services.renderer import Renderer
from app.services.text_fitter import TextFitter


def test_auto_retains_single_span_centring_and_left_aligned_cells(tmp_path):
    source=tmp_path/'centered.pdf'
    with fitz.open() as doc:
        page=doc.new_page(width=400,height=200)
        for y in (20,55,90): page.draw_line((20,y),(380,y))
        for x in (20,200,380): page.draw_line((x,20),(x,90))
        word='Centered heading'
        width=fitz.get_text_length(word,fontsize=12)
        page.insert_text((110-width/2,42),word,fontsize=12)
        page.insert_text((210,42),'Left paragraph',fontsize=12)
        page.insert_text((40,76),'Lower left',fontsize=12)
        page.insert_text((220,76),'Lower right',fontsize=12)
        doc.save(source)
    pages=TextReaderAgent().run(source)
    raw=BlockMakerAgent().run('test',pages)
    blocks=group_blocks(raw,pages,min_spans=4)
    centered=N(**next(b for b in blocks if b['source_text']==word))
    left=next(b for b in blocks if b['source_text']=='Left paragraph')
    assert centered.style['align']=='center'
    assert left['style']['align']=='left'
    assert centered.style.get('rich_text')  # Auto mode allocates complete cell space.
    translation=N(text='Short',target_language='en',script='native',metadata_json={})
    layout=N(**TextFitter().fit(centered,translation.text,400,200,[],None))
    assert layout.bbox==centered.bbox
    destination=tmp_path/'centered-output.pdf'
    Renderer().render_page(source,1,[(centered,translation,layout)],destination)
    with fitz.open(destination) as doc:
        rect=doc[0].search_for('Short')[0]
        assert abs((rect.x0+rect.x1)/2-110)<1


def test_manual_space_padding_does_not_shift_translated_text_left(tmp_path):
    source=tmp_path/'padded.pdf'
    with fitz.open() as doc:
        page=doc.new_page(width=400,height=200)
        page.insert_text((20,50),'                    Padded title ',fontsize=12)
        doc.save(source)
    blocks=BlockMakerAgent().run('test',TextReaderAgent().run(source))
    b=blocks[0]
    assert b['source_text']=='Padded title'
    assert b['style']['source_regions'][0][0]==20
    assert b['bbox'][0]>80
    with fitz.open(source) as doc:
        visible=doc[0].search_for('Padded title')[0]
    assert abs(visible.x0-b['bbox'][0])<.01
    assert abs(visible.x1-b['bbox'][2])<.01
