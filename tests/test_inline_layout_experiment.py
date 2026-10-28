"""Validation contract for the experimental nested-ID strategy."""
import xml.etree.ElementTree as ET
import pytest
from scripts.inline_layout_experiment import group,encode,validate

def sample():return [group('P1',['Keep ','A & B <draft>',' safe.'])]

def test_roundtrip_preserves_entities_and_styles():
 gs=sample();runs=validate(encode(gs),gs)['P1']
 assert [s['text'] for s in runs]==[s['text'] for s in gs[0]['spans']]
 assert [s['bold'] for s in runs]==[False,True,False]

def test_reordered_spans_keep_their_own_styles():
 gs=sample();root=ET.fromstring(encode(gs));root[:]=list(reversed(list(root)))
 runs=validate(ET.tostring(root,encoding='unicode'),gs)['P1']
 assert [s['id'] for s in runs]==[s['id'] for s in reversed(gs[0]['spans'])]
 assert runs[1]['bold']

@pytest.mark.parametrize('mutation',[
 lambda s:s.replace('<span id="P1_s02_r"> safe.</span>',''),
 lambda s:s.replace('P1_s02_r','P1_s01_b'),
 lambda s:s.replace('P1_s02_r','unknown'),
 lambda s:s.replace('id="P1_s00_r"','id="P1_s00_r" bold="false"'),
 lambda s:s.replace('Keep ','<b>Keep</b> '),
 lambda s:'outside'+s,
 lambda s:s.replace('</p>','outside</p>'),
 lambda s:'<!DOCTYPE batch>'+s,
 lambda s:s.replace('P1_s00_r">Keep ','P1_s00_r">'),
])
def test_rejects_corrupt_structure(mutation):
 gs=sample()
 with pytest.raises((ValueError,ET.ParseError)):validate(mutation(encode(gs)),gs)

def test_rejects_span_from_other_parent():
 gs=sample()+[group('P2',['Another ','bold',' phrase.'])]
 text=encode(gs).replace('P1_s01_b','temp').replace('P2_s01_b','P1_s01_b').replace('temp','P2_s01_b')
 with pytest.raises(ValueError):validate(text,gs)

def test_structural_validation_does_not_prove_semantic_style_alignment():
 gs=[group('P1',['The total is ','1250.50 INR',' today.'])]
 text='<p id="P1"><span id="P1_s00_r">Today </span><span id="P1_s01_b">the total is </span><span id="P1_s02_r">1250.50 INR.</span></p>'
 runs=validate(text,gs)['P1']
 assert runs[1]['bold'] and '1250.50' not in runs[1]['text']


def test_sparse_markup_rejects_amount_moved_outside_bold():
 from scripts.test_sparse_inline import validate_sparse
 gs=[group('P1',['The total is ','1250.50 INR',' today.'])]
 text='<p id="P1">Today <b id="P1_s01_b">the total is</b> 1250.50 INR.</p>'
 from app.config import StudioError
 with pytest.raises(StudioError):validate_sparse(text,gs)


def test_sparse_markup_accepts_reordered_prose_with_amount_still_bold():
 from scripts.test_sparse_inline import validate_sparse
 gs=[group('P1',['The total is ','1250.50 INR',' for invoice INV-2048.'])]
 text='<p id="P1">Invoice INV-2048 total: <b id="P1_s01_b">1250.50 INR</b>.</p>'
 runs=validate_sparse(text,gs)['P1']
 assert next(s for s in runs if s['bold'])['text']=='1250.50 INR'


def test_sparse_markup_rejects_duplicate_styled_id():
 from scripts.test_sparse_inline import validate_sparse,encode_sparse
 gs=sample();text=encode_sparse(gs).replace('</p>','<b id="P1_s01_b">extra</b></p>')
 with pytest.raises(ValueError):validate_sparse(text,gs)
