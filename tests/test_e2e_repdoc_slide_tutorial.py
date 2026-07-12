"""End-to-end feature coverage for a slide-deck tutorial style document.

One synthetic landscape slide deck (modeled on vendor power tutorials: slide
titles, spec/thermal/comparison tables, subscripted display formulas, chart
annotations, per-slide wordmark furniture, social footer) is converted once;
each test asserts one planted feature survived. Tests marked ``known_defect``
assert the CORRECT behavior for features the converter breaks today.
"""

import re

import pytest

from tests.e2e_helpers import (
    ConvertedDoc,
    assert_common_invariants,
    convert_doc_for_tests,
    find_rows,
    heading_titles,
    image_targets,
    pua_chars,
    stub_formula_blocks,
    table_rows,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@pytest.fixture(scope="module")
def doc(module_artifact_dir) -> ConvertedDoc:
    from tests.fixtures.representative_docs import write_slide_tutorial_pdf

    pdf = module_artifact_dir / "slide_tutorial_style.pdf"
    return convert_doc_for_tests(write_slide_tutorial_pdf, pdf,
                                 module_artifact_dir / "out")


def _cells(row: str):
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _retained_image_sizes(doc):
    from PIL import Image

    sizes = []
    for target in set(image_targets(doc.text)):
        if not target.startswith(f"{doc.images_dir.name}/"):
            continue
        path = doc.out_md.parent / target
        if path.is_file():
            with Image.open(path) as img:
                sizes.append(img.size)
    return sizes


def test_cover_title_is_heading(doc):
    assert any(doc.manifest["title"] in t for t in heading_titles(doc.text))


def test_slide_titles_are_headings(doc):
    titles = heading_titles(doc.text)
    for slide in doc.manifest["slide_titles"]:
        assert any(slide in t for t in titles), f"slide title lost: {slide}"


def test_cta_text_survives(doc):
    assert doc.manifest["cta"] in doc.text


def test_contents_entries_present(doc):
    for entry, page_no in doc.manifest["contents_entries"]:
        assert entry in doc.text, f"contents entry lost: {entry}"


def test_spec_rows_paired(doc):
    rows = table_rows(doc.plain)
    for row in doc.manifest["spec_rows"]:
        parameter, symbol, unit = row[0], row[2], row[6]
        assert find_rows(rows, parameter, symbol, unit), (
            f"spec row lost or split: {parameter}"
        )


def test_spec_empty_min_typ_not_shifted(doc):
    """Empty Min/Max cells must stay empty — the Typ value must not shift
    into a neighbouring column."""
    rows = table_rows(doc.text)
    header = next(r for r in rows if "Min" in r and "Typ" in r)
    header_cells = _cells(header)
    typ_idx = header_cells.index("Typ")
    row = next(r for r in rows if "Quiescent Current" in r)
    cells = _cells(row)
    assert cells[typ_idx] == "18", f"Typ value shifted: {cells}"
    assert cells[header_cells.index("Min")] == ""
    assert cells[header_cells.index("Max")] == ""


@pytest.mark.known_defect
def test_inequality_conditions_survive(doc):
    """Symbol-font inequality glyphs in table cells must survive as proper
    Unicode (today ≤ is extracted as its byte lookalike '£')."""
    assert "≤ 60 V" in doc.text
    assert "TA ≤ 85°C" in doc.text or "T A ≤ 85°C" in doc.text


def test_thermal_rows_not_merged(doc):
    """Two rows sharing the same symbol/unit must stay separate rows."""
    rows = table_rows(doc.text)
    matches = [r for r in rows if "Junction-to-Air" in r and "°C/W" in r]
    assert len(matches) == 2, f"thermal rows lost or merged: {matches}"


@pytest.mark.known_defect
def test_thermal_footnote_attached(doc):
    """The small-print footnote directly under the table frame must survive
    (on slide layouts it is silently dropped)."""
    assert doc.manifest["thermal_footnote"].lstrip("- ") in doc.text


def test_package_row_cells_not_scrambled(doc):
    """Cells carrying two dimension tokens each must stay in their own
    product column, not bleed across columns."""
    rows = table_rows(doc.text)
    header = next(r for r in rows if all(p in r for p in doc.manifest["comparison_columns"]))
    header_cells = _cells(header)
    row = next(r for r in rows if "Package X/Y (mm)" in r)
    cells = _cells(row)
    for product, expected in doc.manifest["package_row_cells"].items():
        assert cells[header_cells.index(product)] == expected, (
            f"package cell scrambled under {product}: {cells}"
        )


def test_display_formulas_survive(doc):
    """Neither of the two display formulas may be silently lost. Either
    presentation is acceptable per formula: detected as a formula region
    (observable as a recognizer-stub marker) or kept as prose text."""
    blocks = stub_formula_blocks(doc.text)
    assert 1 <= len(blocks) <= 2, f"formula regions: {blocks}"
    if len(blocks) == 1:
        compact = doc.plain.replace(" ", "")
        assert "=(T" in compact, "the second formula was lost entirely"


def test_subscript_prose_content_survives(doc):
    compact = doc.plain.replace(" ", "")
    assert "VOUT" in compact or "V_{OUT}" in compact


def test_small_annotation_labels_stay_plain(doc):
    titles = heading_titles(doc.text)
    for label in doc.manifest["annotation_labels_plain"]:
        assert label in doc.text, f"annotation label lost: {label}"
        assert not any(label in t for t in titles), (
            f"annotation label promoted to heading: {label}"
        )


@pytest.mark.known_defect
def test_bold_overlay_label_not_promoted(doc):
    """A bold text overlay on a figure is a label, not a document heading."""
    label = doc.manifest["annotation_label_bold"]
    assert label in doc.text
    assert not any(label in t for t in heading_titles(doc.text)), (
        "figure overlay label was promoted to a heading"
    )


def test_repeated_footer_removed_as_furniture(doc):
    """The per-slide social/copyright footer line is page furniture; the
    layout stage removes it at document level."""
    assert doc.manifest["footer_left"] not in doc.text
    assert doc.manifest["footer_right"] not in doc.text


@pytest.mark.known_defect
def test_per_slide_furniture_images_removed(doc):
    """The wordmark strip and logo block repeated on every slide are page
    furniture: no retained image may be a small repeated mark (every retained
    content image here — the chart — is well over 40 px tall; the furniture
    crops are not)."""
    small = [(w, h) for w, h in _retained_image_sizes(doc) if h <= 40]
    assert small == [], f"per-slide furniture images retained: {small}"


def test_chart_image_retained(doc):
    assert any(w >= 250 for w, h in _retained_image_sizes(doc)), (
        "the chart image was dropped"
    )


def test_winansi_glyph_prose_fidelity(doc):
    assert "±10%" in doc.text
    assert "85°C" in doc.text
    assert doc.manifest["copyright_line"] in doc.text


@pytest.mark.known_defect
def test_symbol_glyph_prose_fidelity(doc):
    """Symbol-font glyphs in prose must survive as proper Unicode (today
    they are extracted as byte lookalikes: ≤ as '£', θ as 'q', ≅ as '@')."""
    assert "≤ 30 µV" in doc.text
    assert "≅" in doc.text
    assert "θJA" in doc.text.replace(" ", "")


def test_no_pua_codepoints(doc):
    assert pua_chars(doc.text) == []


def test_common_invariants(doc):
    assert_common_invariants(doc)
