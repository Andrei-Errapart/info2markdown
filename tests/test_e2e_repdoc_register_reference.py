"""End-to-end feature coverage for a register-reference style document.

One synthetic register-reference PDF (modeled on vendor register-reference
datasheets: page-header doc codes, a multi-page register list with repeated
headers, a wide register-description table with vertically spanning address
cells) is converted once through the real pipeline; each test asserts that one
planted feature survived. Tests marked ``known_defect`` assert the CORRECT
behavior for features the converter breaks today.
"""

import re

import pytest

from tests.e2e_helpers import (
    ConvertedDoc,
    assert_common_invariants,
    convert_doc_for_tests,
    find_rows,
    heading_titles,
    headings,
    image_targets,
    table_rows,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@pytest.fixture(scope="module")
def doc(module_artifact_dir) -> ConvertedDoc:
    from tests.fixtures.representative_docs import write_register_reference_pdf

    pdf = module_artifact_dir / "regref_style.pdf"
    return convert_doc_for_tests(write_register_reference_pdf, pdf,
                                 module_artifact_dir / "out")


def test_title_is_heading(doc):
    assert any(doc.manifest["title"] in t for t in heading_titles(doc.text))


def test_section_headings_present(doc):
    titles = heading_titles(doc.text)
    for section in doc.manifest["section_headings"]:
        assert any(section in t for t in titles), f"missing section heading: {section}"


def test_heading_count_sane(doc):
    found = headings(doc.text)
    assert len(found) <= doc.manifest["max_headings"], (
        f"heading explosion: {found}"
    )


def test_header_doc_code_absent(doc):
    """The repeated page-header document code is page furniture and must not
    survive into the output."""
    assert doc.manifest["doc_code"] not in doc.text


def test_confidentiality_line_absent(doc):
    """The repeated confidentiality boilerplate line is page furniture."""
    assert doc.manifest["confidential_line"] not in doc.text


def test_interior_page_numbers_absent(doc):
    """Per-page footer page numbers must not survive as bare-number lines
    (the layout stage drops them at document level)."""
    assert re.search(r"(?m)^\s*[0-9]{1,3}\s*$", doc.text) is None


def test_address_range_rows_paired(doc):
    rows = table_rows(doc.plain)
    for rng, desc in doc.manifest["address_ranges"]:
        assert find_rows(rows, rng, desc), f"range row lost or split: {rng}"


def test_overlapping_ranges_preserved_verbatim(doc):
    """A source document that contradicts itself (overlapping address ranges)
    must be reproduced verbatim, not 'corrected'."""
    a, b = doc.manifest["overlap_pair"]
    assert a in doc.text and b in doc.text


def test_register_list_complete_across_page_break(doc):
    rows = table_rows(doc.plain)
    missing = [
        r["name"] for r in doc.manifest["register_list_rows"]
        if not find_rows(rows, r["dec_hex"], r["name"], r["default"])
    ]
    assert missing == [], f"register list rows lost in conversion: {missing}"
    first, second = doc.manifest["page_break_pair"]
    assert find_rows(rows, first["name"]) and find_rows(rows, second["name"])


def test_bit_patterns_verbatim(doc):
    for pattern in {r["fmt"] for r in doc.manifest["register_list_rows"]}:
        assert pattern in doc.text, f"bit pattern column corrupted: {pattern}"


def test_continuation_rows_not_duplicated(doc):
    """Repeating the table header on the continuation page must not duplicate
    data rows."""
    rows = table_rows(doc.plain)
    for r in doc.manifest["register_list_rows"]:
        matches = find_rows(rows, r["dec_hex"], r["name"])
        assert len(matches) == 1, f"duplicated row: {matches}"


@pytest.mark.known_defect
def test_desc_block_top_and_trailer_present(doc):
    """Register-block content must survive: when blocks with spanning address
    cells merge, the tail of the merged cell text (the last block's summary
    trailer and field descriptions) is silently lost."""
    for block in doc.manifest["desc_blocks"]:
        bits, default, name = block["top"]
        assert name in doc.plain, f"register top row lost: {name}"
        assert block["trailer"] in doc.plain, f"summary trailer lost: {block['trailer']}"


@pytest.mark.known_defect
def test_desc_block_bitfield_rows_not_merged(doc):
    """Bit-field rows under a vertically spanning address cell must stay
    separate rows with their enumerated-value lines."""
    rows = table_rows(doc.plain)
    assert find_rows(rows, "14:12", "HDR_MODE"), "bit 14:12 row lost or merged"
    for row in find_rows(rows, "HDR_MODE"):
        assert "HDR_CONTROL0" not in row, f"bit rows merged into top row: {row}"
    block = doc.manifest["desc_blocks"][0]
    for line in block["bits"][1][3]:
        assert line in doc.plain, f"enumerated value line lost: {line}"


def test_verilog_literals_and_identifiers(doc):
    for token in doc.manifest["verilog_tokens"]:
        assert token in doc.plain
    assert "x_addr_start" in doc.plain


def test_source_typo_preserved(doc):
    assert doc.manifest["typo"] in doc.text


@pytest.mark.known_defect
def test_hyphen_wrapped_url_rejoined(doc):
    """A URL wrapped at a hyphen across a line break must be rejoined with
    its hyphen intact."""
    assert doc.manifest["urls"]["wrapped"] in doc.plain
    assert re.search(r"(?m)^- Marking", doc.plain) is None


def test_intact_url_preserved(doc):
    assert doc.manifest["urls"]["plain"] in doc.plain


def test_logo_image_kept(doc):
    targets = [t for t in image_targets(doc.text)
               if t.startswith(f"{doc.images_dir.name}/")]
    assert targets, "the title-page logo image was dropped"


def test_common_invariants(doc):
    assert_common_invariants(doc)
