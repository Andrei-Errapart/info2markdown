"""End-to-end feature coverage for a hardware-manual chapter style document.

One synthetic manual chapter (modeled on SoC hardware manuals: numbered
heading ladder, per-page header logo sliver and doc-code footer, register
list with notes, bit-description block, procedure table with spanner rows,
a (1/2)+(2/2) electrical table, a transition matrix with dingbat marks,
subscripted formulas, a numbered flow with lettered sub-items) is converted
once; each test asserts one planted feature survived. Tests marked
``known_defect`` assert the CORRECT behavior for features that break today.

Glyph tolerance: the PDF text layer may surface Symbol-font glyphs as
compatibility codepoints (Δ as U+2206 INCREMENT, Ω as U+2126 OHM SIGN);
assertions accept either form.
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

DELTA_FORMS = ("\u0394", "\u2206")   # greek delta, increment
OMEGA_FORMS = ("\u03a9", "\u2126")   # greek omega, ohm sign


@pytest.fixture(scope="module")
def doc(module_artifact_dir) -> ConvertedDoc:
    from tests.fixtures.representative_docs import write_hardware_manual_pdf

    pdf = module_artifact_dir / "hardware_manual_style.pdf"
    return convert_doc_for_tests(write_hardware_manual_pdf, pdf,
                                 module_artifact_dir / "out")


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


def test_chapter_band_heading(doc):
    assert any(doc.manifest["chapter_band"] in t for t in heading_titles(doc.text))


def test_numbered_heading_ladder(doc):
    titles = heading_titles(doc.plain)
    for number, title in doc.manifest["numbered_headings"]:
        assert any(number in t and title in t for t in titles), (
            f"numbered heading lost: {number} {title}"
        )


def test_opening_sentence_present(doc):
    assert doc.manifest["opening_sentence"] in doc.text


def test_register_list_rows_complete(doc):
    rows = table_rows(doc.plain)
    for row in doc.manifest["register_list_rows"]:
        name, abbrev, initial = row[0], row[1], row[2].split(" *")[0]
        assert find_rows(rows, name, abbrev, initial), (
            f"register list row lost: {name}"
        )


def test_note_blocks_survive(doc):
    for note in doc.manifest["notes"]:
        assert note in doc.text, f"table note lost: {note}"


def test_runin_metadata_present(doc):
    for label, value in doc.manifest["runin_pairs"]:
        assert label.rstrip(" :") in doc.plain, f"run-in label lost: {label}"
        assert value in doc.plain, f"run-in value lost: {value}"


def test_bit_diagram_image_retained(doc):
    assert any(w >= 300 for w, h in _retained_image_sizes(doc)), (
        "the register bit-diagram image was dropped"
    )


def test_bit_value_colon_spacing(doc):
    for packed in doc.manifest["bit_rows_packed"]:
        assert packed in doc.plain, f"packed bit-value cell corrupted: {packed}"


def test_undefined_footnote_survives(doc):
    assert doc.manifest["undefined_footnote"] in doc.text


def test_phase_spanner_rows_sane(doc):
    """A full-width phase row must survive as exactly one table row (the
    canonical serialization repeats the text across the row's cells)."""
    rows = table_rows(doc.text)
    for spanner in doc.manifest["procedure"]["spanners"]:
        matching = [r for r in rows if spanner in r]
        assert len(matching) == 1, f"spanner row lost or duplicated: {matching}"


def test_reg_field_tokens_verbatim(doc):
    for token in doc.manifest["procedure"]["reg_tokens"]:
        assert token in doc.plain, f"register-field token corrupted: {token}"


@pytest.mark.known_defect
def test_emdash_empty_cells_survive(doc):
    """Em-dash 'no value' cells must keep their em dash (today it is
    flattened to an ASCII hyphen)."""
    assert "—" in doc.text, "em-dash empty cells were flattened or dropped"
    rows = table_rows(doc.text)
    assert any("Release the PLL1 reset" in r and "—" in r for r in rows)


def test_display_formulas_survive(doc):
    """Display formulas must survive: either detected as formula regions
    (observable as the recognizer stub's markers) or kept as prose text."""
    blocks = stub_formula_blocks(doc.text)
    if blocks:
        assert len(blocks) == 2, f"formula regions detected: {blocks}"
    else:
        assert "65536" in doc.plain


def test_electrical_all_rows_present(doc):
    rows = table_rows(doc.plain)
    for row in doc.manifest["electrical"]["rows"]:
        item, symbol = row[0], row[2]
        assert find_rows(rows, item, symbol), f"electrical row lost: {item}"
    for caption in doc.manifest["electrical"]["captions"]:
        assert caption.split("  ")[0] in doc.text, f"caption lost: {caption}"


def test_electrical_header_not_duplicated_as_data(doc):
    rows = table_rows(doc.text)
    header_rows = find_rows(rows, "Item", "I/O Type", "Symbol")
    assert 1 <= len(header_rows) <= 2, (
        f"unexpected electrical header rows: {header_rows}"
    )


@pytest.mark.known_defect
def test_wrapped_merged_header_joined(doc):
    """Header pin names wrapped inside one cell must be rejoined without a
    space (MD_ / BOOT_2 -> MD_BOOT_2)."""
    assert doc.manifest["boot_mode_header_joined"] in doc.plain
    assert "MD_ BOOT_2" not in doc.plain


@pytest.mark.known_defect
def test_minus_sign_negatives(doc):
    """Minus-sign negatives must keep U+2212 (today they are flattened to a
    spaced ASCII hyphen: '−0.3' becomes '- 0.3')."""
    for cell in doc.manifest["electrical"]["minus_cells"]:
        assert cell in doc.text, f"minus-sign negative corrupted: {cell}"


def test_unit_glyphs(doc):
    assert "µA" in doc.text
    assert "°C" in doc.text


@pytest.mark.known_defect
def test_kilo_ohm_unit_survives(doc):
    """The Symbol-font ohm glyph must survive as Ω (today it is extracted as
    its byte lookalike, leaving 'k W')."""
    assert any(f"k{omega}" in doc.text for omega in OMEGA_FORMS), (
        "kilo-ohm unit corrupted"
    )


@pytest.mark.known_defect
def test_transition_matrix_marks(doc):
    """Dingbat transition marks must survive as proper Unicode (today they
    are extracted as byte lookalikes: ✓ as '3', ✗ as '7', ● as 'l', Δ as 'D')."""
    rows = table_rows(doc.text)
    sleep_rows = find_rows(rows, "Sleep", "✗")
    assert sleep_rows and any("●" in r for r in sleep_rows)
    assert any("✓" in r for r in rows)
    assert any(any(d in r for d in DELTA_FORMS) for r in rows), (
        "conditional-transition delta mark lost"
    )
    assert any("×" in r for r in rows)


@pytest.mark.known_defect
def test_transition_matrix_legend(doc):
    """Legend lines must keep their dingbat marks (same byte-lookalike
    corruption as the matrix cells)."""
    for line in doc.manifest["matrix"]["legend"]:
        variants = [line] + [line.replace("Δ", d) for d in DELTA_FORMS]
        assert any(v in doc.text for v in variants), f"legend line lost: {line}"


def test_no_pua_codepoints(doc):
    assert pua_chars(doc.text) == []


def test_boot_list_items_present(doc):
    for item in doc.manifest["boot_list"]["items"]:
        assert item in doc.text, f"flow item lost: {item}"
    for sub in doc.manifest["boot_list"]["subitems"]:
        assert sub in doc.text, f"flow sub-item lost: {sub}"


@pytest.mark.known_defect
def test_boot_sublist_not_renumbered(doc):
    """Lettered sub-items must not be promoted to numbered top-level steps
    (which mis-numbers the following steps)."""
    assert re.search(r"(?m)^\s*\d+\.\s*\(A\)", doc.text) is None, (
        "lettered sub-item was promoted to a numbered step"
    )


@pytest.mark.known_defect
def test_caution_compound_hyphen_preserved(doc):
    """A hyphenated compound wrapped at the line break must keep its hyphen
    when the lines are rejoined."""
    assert doc.manifest["caution"]["compound"] in doc.text
    assert "AWOOTHERS" not in doc.text


def test_source_typo_preserved(doc):
    assert doc.manifest["typo"] in doc.text


def test_footer_doc_code_absent(doc):
    """The per-page footer document code is page furniture; the layout stage
    drops it at document level (the text-level cleanup rule is covered
    separately by the fast suite)."""
    assert doc.manifest["footer_code"] not in doc.text


@pytest.mark.known_defect
def test_header_sliver_removed(doc):
    """The repeated header logo sliver is page furniture and must not be
    retained (today one cropped instance survives; every legitimate content
    image in this document is well over 40 px tall)."""
    slivers = [(w, h) for w, h in _retained_image_sizes(doc) if h <= 40]
    assert slivers == [], f"header slivers retained: {slivers}"


def test_common_invariants(doc):
    assert_common_invariants(doc)
