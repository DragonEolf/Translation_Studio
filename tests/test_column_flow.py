from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as N
import pymupdf as fitz
import pytest
from app.services.column_flow import ColumnFlow
from app.services.layout_tools import fits
from app.services.text_fitter import FontRegistry


def entry(identity,y,text,size=12,language='en'):
    b=N(id=identity,type='paragraph',reading_order=y,source_text=text,bbox=[30,y,290,y+55],
        style={'size':12,'color':0,'rich_text':{'version':1},'line_height':1.25,
               'layout_policy':{'decision':'reflow','role':'prose','source_bbox':[30,y,290,y+40]}})
    t=N(text=text,target_language=language,script='native',metadata_json={'rich_text':{'runs':[{'text':text,'style':{'size':12,'color':0}}]}})
    l=N(bbox=list(b.bbox),font='sans-serif',font_size=size,fit_status='fitted',line_breaks=[])
    return b,t,l


def test_joint_measured_fit_can_grow_small_paragraph_without_changing_text():
    entries=[entry('a',30,'A longer paragraph with several words to wrap across lines. '*2,8),entry('b',90,'A short paragraph.')]
    originals=deepcopy([(b.style,t.text,t.metadata_json) for b,t,_ in entries])
    fonts=FontRegistry();page=N(width=340,height=220)
    report=ColumnFlow(fonts).apply(entries,[e[0] for e in entries],page)
    assert report['changed_blocks'] and entries[0][2].font_size>8
    assert report['measurements']<=report['measurement_budget']
    assert all(fits(b,t,l,fonts,page) for b,t,l in entries)
    assert (fitz.Rect(entries[0][2].bbox)&fitz.Rect(entries[1][2].bbox)).get_area()==0
    assert [(b.style,t.text,t.metadata_json) for b,t,_ in entries]==originals


@pytest.mark.parametrize('barrier',['heading','rule','image','fixed'])
def test_dp_never_crosses_anchored_content(barrier):
    entries=[entry('a',30,'Some paragraph words. '*4,8),entry('b',90,'More paragraph words.')]
    before=deepcopy([vars(l) for _,_,l in entries]);graphics=[];blocks=[e[0] for e in entries]
    if barrier=='fixed':entries[1][0].style['layout_policy']['decision']='anchor'
    elif barrier=='heading':
        blocks.append(N(id='heading',bbox=[30,86,290,92],style={}))
    else:graphics=[[30,86,290,86 if barrier=='rule' else 92]]
    report=ColumnFlow(FontRegistry()).apply(entries,blocks,N(width=340,height=220),graphics)
    assert report['changed_blocks']==[]
    assert [vars(l) for _,_,l in entries]==before


def test_dp_measurement_budget_and_readable_neighbor_floor():
    entries=[entry('a',30,'Long text. '*20,6),entry('b',90,'Short text.',12)]
    report=ColumnFlow(FontRegistry(),max_measurements=0).apply(entries,[e[0] for e in entries],N(width=340,height=220))
    assert report['changed_blocks']==[] and report['measurements']==0
    report=ColumnFlow(FontRegistry()).apply(entries,[e[0] for e in entries],N(width=340,height=220))
    assert entries[1][2].font_size>=10.8


def test_tamil_uses_actual_shaped_measurements():
    fonts=FontRegistry(Path('fonts'))
    if fonts.resolve('ta','native')=='missing':pytest.skip('Tamil font missing')
    entries=[entry('a',30,'உயிரியல் ஆராய்ச்சி மற்றும் கணினி முறைகள். '*2,8,'ta'),entry('b',90,'ஆராய்ச்சி முறைகள்.',12,'ta')]
    for _,t,l in entries:l.font=fonts.resolve('ta','native')
    report=ColumnFlow(fonts).apply(entries,[e[0] for e in entries],N(width=340,height=220))
    assert report['changed_blocks']
    assert all(fits(b,t,l,fonts,N(width=340,height=220)) for b,t,l in entries)
