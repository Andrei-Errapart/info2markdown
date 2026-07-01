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
    from PIL import Image
    monkeypatch.setattr(unimernet_formula, "recognize", lambda img: "R_{BLKupper}")
    from unimernet_formula import UniMERNetFormulaModel
    model = UniMERNetFormulaModel()
    item = types.SimpleNamespace(text="stale")
    el = types.SimpleNamespace(item=item, image=Image.new("L", (100, 20), 255))
    out = list(model(doc=None, element_batch=[el]))
    assert item.text == "R_{BLKupper}"
    assert out == [item]


def test_formula_model_appends_tag(monkeypatch):
    from PIL import Image, ImageDraw
    from unimernet_formula import UniMERNetFormulaModel
    monkeypatch.setattr(unimernet_formula, "recognize", lambda img: "x=1")
    monkeypatch.setattr(unimernet_formula, "read_equation_number", lambda img: "12")
    # Formula block on the left, a small number block far to the right.
    img = Image.new("L", (1000, 100), 255)
    d = ImageDraw.Draw(img)
    d.rectangle([20, 30, 400, 70], fill=0)
    d.rectangle([900, 40, 950, 60], fill=0)
    item = types.SimpleNamespace(text="")
    el = types.SimpleNamespace(item=item, image=img)
    list(UniMERNetFormulaModel()(doc=None, element_batch=[el]))
    assert item.text == "x=1 \\tag{12}"


def test_split_separates_right_margin_number():
    from PIL import Image, ImageDraw
    from unimernet_formula import split_formula_and_number
    img = Image.new("L", (1000, 100), 255)
    d = ImageDraw.Draw(img)
    d.rectangle([20, 30, 400, 70], fill=0)    # formula (wide, left)
    d.rectangle([900, 40, 950, 60], fill=0)   # number (small, far right)
    formula, number = split_formula_and_number(img)
    assert number is not None
    assert formula.width < 500                # cropped to the formula
    assert number.width < formula.width       # number is the small appendage


def test_split_no_number_just_trims():
    from PIL import Image, ImageDraw
    from unimernet_formula import split_formula_and_number
    img = Image.new("L", (600, 100), 255)
    ImageDraw.Draw(img).rectangle([20, 30, 550, 70], fill=0)  # single wide block
    formula, number = split_formula_and_number(img)
    assert number is None
    assert formula.width <= 540               # trimmed to the ink bbox


def test_build_pdf_converter_uses_custom_pipeline():
    from unimernet_formula import build_pdf_converter, UniMERNetPdfPipeline
    from docling.datamodel.base_models import InputFormat
    conv = build_pdf_converter(ocr=False)
    fmt = conv.format_to_options[InputFormat.PDF]
    assert fmt.pipeline_cls is UniMERNetPdfPipeline
    assert fmt.pipeline_options.do_formula_enrichment is False
    assert fmt.pipeline_options.do_table_structure is True
    assert fmt.pipeline_options.do_ocr is False


def test_build_pdf_converter_ocr_flag():
    from unimernet_formula import build_pdf_converter
    from docling.datamodel.base_models import InputFormat
    fmt = build_pdf_converter(ocr=True).format_to_options[InputFormat.PDF]
    assert fmt.pipeline_options.do_ocr is True
