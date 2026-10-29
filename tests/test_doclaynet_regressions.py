"""Small, offline regressions reduced from the DocLayNet evaluation."""
from types import SimpleNamespace as N
import pymupdf as fitz
import pytest
from app.agents.text_reader import TextReaderAgent
from app.agents.block_maker import BlockMakerAgent
from app.services.rich_text import group_blocks, source_runs
from app.services.renderer import Renderer
from app.services.text_fitter import TextFitter


def test_separate_whitespace_spans_survive_grouping_without_splitting_styled_words(tmp_path):
    source=tmp_path/'spaces.pdf'
    with fitz.open() as pdf:
        page=pdf.new_page(width=500,height=200)
        x=30
        # Different fonts prevent the PDF extractor coalescing explicit spaces.
        for text,font in [('LTE','helv'),(' ','hebo'),('network','helv'),(' ','hebo'),
                          ('catering','helv'),(' ','hebo'),('style','helv'),('split','hebo')]:
            page.insert_text((x,60),text,fontname=font,fontsize=12)
            x+=fitz.get_text_length(text,fontname=font,fontsize=12)
        pdf.save(source)
    pages=TextReaderAgent().run(source)
    original=BlockMakerAgent().run('d',pages)
    assert any(s['text'].isspace() for b in pages[0]['blocks'] if b['type']==0
               for l in b['lines'] for s in l['spans'])
    for threshold in [1,4]:
        grouped=group_blocks(original,pages,min_spans=threshold)
        assert len(grouped)==1
        assert grouped[0]['source_text']=='LTE network catering stylesplit'
        runs=source_runs(N(**grouped[0]))
        assert ''.join(r['text'] for r in runs)=='LTE network catering stylesplit'
        assert any(r['text']=='split' and r['style']['bold'] for r in runs)
        assert len(grouped[0]['style']['rich_text']['source_spans'])==len(original)


@pytest.mark.parametrize('text',['• abc x','• abc z','• abc x  ','• example','∑ x'])
def test_actual_text_roundtrips_when_replacement_ends_with_placeholder(tmp_path,text):
    source=tmp_path/'source.pdf'
    with fitz.open() as pdf:
        pdf.new_page(width=300,height=200)
        pdf.save(source)
    block=N(bbox=[30,30,250,80],style={'size':12,'color':0,'fixed_box':True})
    translation=N(text=text,target_language='en',script='native')
    layout=N(**TextFitter().fit(block,text,300,200,[],None))
    assert layout.fit_status=='fitted'
    target=tmp_path/'result.pdf'
    Renderer().render_page(source,1,[(block,translation,layout)],target)
    with fitz.open(target) as pdf:
        assert pdf[0].get_text().strip()==text.strip()


def test_ligature_comparison_does_not_erase_mathematical_distinctions():
    from app.agents.checker import normalized
    assert normalized('Eﬀects and conﬁgs')==normalized('Effects and configs')
    assert normalized('x²')!=normalized('x2')
    assert normalized('①')!=normalized('1')


@pytest.mark.parametrize('moved,code',[(False,'source_geometry_overlap'),(True,'collision')])
def test_checker_distinguishes_inherited_and_increased_overlap(tmp_path,moved,code):
    from app.agents.checker import CheckerAgent
    source=tmp_path/'source.pdf'
    with fitz.open() as pdf:
        pdf.new_page(width=300,height=200)
        pdf.save(source)
    entries=[]
    for name,box in [('a',[30,30,120,70]),('b',[110,30,200,70])]:
        block=N(id=name,bbox=box,source_text='Text',style={'size':10,'color':0})
        layout=N(bbox=list(box),font_size=10,font='sans-serif',fit_status='fitted')
        if moved and name=='b':layout.bbox=[90,30,180,70]
        entries.append((block,N(text='Text'),layout))
    target=tmp_path/'target.pdf'
    Renderer().render_page(source,1,entries,target)
    issues=CheckerAgent().run(source,target,1,entries)
    overlaps=[i for i in issues if i['code'] in {'collision','source_geometry_overlap'}]
    assert len(overlaps)==1 and overlaps[0]['code']==code
    assert overlaps[0]['severity']==('error' if moved else 'warning')


@pytest.mark.parametrize('name,expected',[('FranklinGothic-DemiCond',True),('Arial-BoldMT',True),
    ('NotoSans-SemiBold',True),('BlackadderITC',False),('ArialMT',False)])
def test_explicit_weight_suffix_recovers_missing_pdf_bold_flag(name,expected):
    from app.agents.block_maker import span_style
    style=span_style([{'font':name,'size':12,'color':0,'flags':0,'text':'Heading'}])
    assert style['bold']==expected


def test_synthetic_expansion_protects_literal_across_style_boundaries(tmp_path):
    from benchmarks.doclaynet.run import canonical_replacements
    source=tmp_path/'split-code.pdf'
    with fitz.open() as pdf:
        p=pdf.new_page(width=400,height=200)
        x=30
        for text,font in [('Invoice ', 'helv'),('INV-', 'heit'),('2048', 'hebo'),(' approved', 'helv')]:
            p.insert_text((x,60),text,fontname=font,fontsize=12)
            x+=fitz.get_text_length(text,fontname=font,fontsize=12)
        pdf.save(source)
    pages=TextReaderAgent().run(source)
    blocks=[N(**b) for b in BlockMakerAgent().run('d',pages)]
    replacements=canonical_replacements(blocks,pages,'expand150')
    assert replacements[next(b.id for b in blocks if b.source_text=='INV-')]=='INV-'
    assert replacements[next(b.id for b in blocks if b.source_text=='2048')]=='2048'
    assert replacements[next(b.id for b in blocks if b.source_text=='Invoice')]!='Invoice'
