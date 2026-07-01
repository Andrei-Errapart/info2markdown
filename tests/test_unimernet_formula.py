"""Tests for the UniMERNet-base formula OCR module (offline; no model load)."""

import types

import unimernet_formula
from unimernet_formula import strip_eqno


def test_strip_eqno_removes_trailing_equation_number():
    assert strip_eqno(r"k _ { B L K } = 3 6 5 \eqno ( 4 5 )") == r"k _ { B L K } = 3 6 5"
    assert strip_eqno(r"a = b \eqno{(12)}") == "a = b"


def test_strip_eqno_noop_without_eqno():
    assert strip_eqno(r"R _ { B L K u p p e r } = 1 5 . 1 7 \, M \Omega") == \
        r"R _ { B L K u p p e r } = 1 5 . 1 7 \, M \Omega"


def test_formula_model_writes_latex_to_item(monkeypatch):
    monkeypatch.setattr(unimernet_formula, "recognize", lambda img: "R_{BLKupper}")
    from unimernet_formula import UniMERNetFormulaModel
    model = UniMERNetFormulaModel()
    item = types.SimpleNamespace(text="stale")
    el = types.SimpleNamespace(item=item, image="fake-crop")
    out = list(model(doc=None, element_batch=[el]))
    assert item.text == "R_{BLKupper}"
    assert out == [item]


def test_build_pdf_converter_uses_custom_pipeline():
    from unimernet_formula import build_pdf_converter, UniMERNetPdfPipeline
    from docling.datamodel.base_models import InputFormat
    conv = build_pdf_converter(ocr=False)
    fmt = conv.format_to_options[InputFormat.PDF]
    assert fmt.pipeline_cls is UniMERNetPdfPipeline
    assert fmt.pipeline_options.do_formula_enrichment is False
    assert fmt.pipeline_options.do_table_structure is True
