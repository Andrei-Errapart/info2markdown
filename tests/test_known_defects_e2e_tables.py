"""Known table defects, reproduced through the real PDF pipeline.

These conversion defects originate in the PDF-extraction stage itself (table
rows merged and cells scrambled, bold captions promoted to headings), so they
cannot be reproduced by feeding pre-converted Markdown through the
post-processing passes: the damage is already done by then. Each test builds a
synthetic PDF with the print layout that loses content on real datasheets,
runs the real conversion, and asserts the CORRECT output — failing until the
pipeline is fixed.

Layouts that did NOT reproduce their defect (and therefore carry no test):
multi-column gain-table flows across page breaks extract with zero dropped
rows, and small-print ``Note N.`` lines under a table survive even at 5.5 pt
touching the table frame. Row merging reproduces only when the table has no
inner horizontal rules.

The tests run docling's layout/table models (downloaded on first use) but stub
the formula recognizer so the large formula model is never loaded; the
synthetic pages contain no formulas.
"""

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.slow, pytest.mark.known_defect]


def _convert_pdf(pdf: Path, out_dir: Path, monkeypatch) -> str:
    try:
        import docling  # noqa: F401
    except ModuleNotFoundError:
        pytest.skip("docling is not installed")
    import pdf2markdown
    import unimernet_formula

    # The synthetic pages contain no formulas; make sure a stray FORMULA
    # detection can never pull in the large recognizer model.
    monkeypatch.setattr(unimernet_formula, "recognize", lambda img: "")

    _, out_md, _, _ = pdf2markdown.convert(
        pdf, out_dir, ocr=False, force=False, postprocess=False,
    )
    return out_md.read_text(encoding="utf-8")


def _write_pdf(builder, path: Path, **kwargs):
    try:
        return builder(path, **kwargs)
    except ModuleNotFoundError as exc:
        pytest.skip(f"fixture dependency unavailable: {exc.name}")


def test_wide_register_bitfield_rows_not_merged(artifact_dir: Path, monkeypatch):
    """In a wide register-description table without inner row rules (a common
    datasheet style), adjacent bit-field rows must stay separate rows with
    their enumerated-value lines. Extraction merges and scrambles them: names
    and defaults from neighbouring rows fuse into one cell and wrapped value
    lines are lost."""
    from tests.fixtures.known_defect_fixtures import write_register_description_pdf

    pdf = artifact_dir / "register_description.pdf"
    _write_pdf(write_register_description_pdf, pdf, ruled_rows=False)

    text = _convert_pdf(pdf, artifact_dir / "out", monkeypatch)

    rows = [line for line in text.splitlines() if line.startswith("|")]
    for bit, name in [
        ("15", "HDR_EN"),
        ("14:12", "HDR_MODE"),
        ("11", "HDR_T2_EN"),
        ("10:8", "HDR_RATIO"),
    ]:
        matching = [r for r in rows if f" {bit} " in r and name in r]
        assert matching, f"no table row pairs bit {bit} with field {name}:\n" + "\n".join(rows)
    mode_rows = [r for r in rows if "HDR_MODE" in r]
    assert not any("HDR_EN" in r for r in mode_rows), (
        f"adjacent bit-field rows merged: {mode_rows}"
    )
    assert "linearize / bypass T1 / bypass T2" in text
    assert "'3'b100 - 2-exposure linearize" in text


def test_bold_table_caption_not_promoted_to_heading(artifact_dir: Path, monkeypatch):
    """A bold standalone ``Table N.`` caption above a table must stay caption
    text, not become a document heading."""
    from tests.fixtures.known_defect_fixtures import write_caption_table_pdf

    pdf = artifact_dir / "caption_table.pdf"
    _write_pdf(write_caption_table_pdf, pdf)

    text = _convert_pdf(pdf, artifact_dir / "out", monkeypatch)

    assert re.search(r"(?m)^#{1,6}\s+Table \d+\.", text) is None, (
        "table caption was promoted to a heading"
    )
    assert "TRIGGER MODES" in text
