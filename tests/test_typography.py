from copy import deepcopy
from types import SimpleNamespace as N
import pymupdf as fitz
from app.services.text_fitter import FontRegistry
from app.services.typography import TypographyPass, diagnostics, neighbour_edges, penalty
from app.services.layout_tools import minimum_body_size, fits


def entry(i, source=12, size=12, text='Short body text', cell=None, kind='paragraph'):
    y=30+i*55
    style={'size':source,'color':0,'bold':False,'italic':False,'align':'left'}
    if cell:style['table_cell_bbox']=list(cell)
    b=N(id=str(i),type=kind,bbox=[30,y,270,y+40],style=style)
    t=N(text=text,target_language='en',script='native',metadata_json={})
    l=N(bbox=list(b.bbox),font='sans-serif',font_size=size,fit_status='fitted',line_breaks=[])
    return b,t,l


def test_local_outlier_improves_without_moving_boxes_or_changing_text():
    entries=[entry(0),entry(1,size=6),entry(2)]
    before=penalty(entries,neighbour_edges(entries));boxes=deepcopy([l.bbox for _,_,l in entries]);texts=[t.text for _,t,_ in entries]
    fonts=FontRegistry();page=N(width=400,height=300)
    report=TypographyPass(fonts,max_refits=3).apply(entries,page)
    assert report['changed_blocks'] and report['refits']<=3
    assert entries[1][2].font_size>6
    assert penalty(entries,neighbour_edges(entries))<before
    assert boxes==[l.bbox for _,_,l in entries] and texts==[t.text for _,t,_ in entries]
    assert all(fits(b,t,l,fonts,page) for b,t,l in entries)


def test_intentional_size_ratios_and_superscript_sizes_are_preserved():
    entries=[entry(0,source=16,size=12),entry(1,source=10,size=7.5)]
    assert TypographyPass(FontRegistry()).apply(entries,N(width=400,height=300))['changed_blocks']==[]
    b,t,l=entries[0]
    t.metadata_json={'rich_text':{'runs':[{'text':'Body','style':{'size':16}}, {'text':'1','style':{'size':8,'superscript':True}}]}}
    assert minimum_body_size(b,t,l.font_size)==12
    assert l.font_size*8/16==6


def test_no_cross_cell_or_heading_body_smoothing():
    assert neighbour_edges([entry(0,cell=(20,20,300,80)),entry(1,cell=(20,80,300,150))])==[]
    assert neighbour_edges([entry(0,kind='heading'),entry(1)])==[]


def test_unfixable_tiny_outlier_does_not_drag_neighbours_down():
    entries=[entry(0),entry(1,size=2,text='Long content '*400),entry(2)]
    report=TypographyPass(FontRegistry(),min_size=6,max_refits=2).apply(entries,N(width=400,height=300))
    assert report['refits']<=2
    assert entries[0][2].font_size==12 and entries[2][2].font_size==12
    assert any(code=='small_text' for _,code,_ in diagnostics(entries))


def test_refit_budget_zero_disables_measurements():
    entries=[entry(0),entry(1,size=6)]
    assert TypographyPass(FontRegistry(),max_refits=0).apply(entries,N(width=400,height=300))['refits']==0
