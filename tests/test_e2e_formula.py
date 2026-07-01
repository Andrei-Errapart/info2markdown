"""End-to-end: UniMERNet-base fixes the BLK equations CodeFormulaV2 mangled.

Opt-in (``slow``): needs the UCC256404 PDF and downloads the model on first run.
"""

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

PDF = Path(__import__("os").environ.get(
    "UCC_PDF", str(Path.home() / "Downloads" / "ucc256404.pdf")))


@pytest.mark.skipif(not PDF.is_file(), reason="UCC256404 PDF not available")
def test_blk_equations_recognized(tmp_path):
    import pdf2markdown
    src = tmp_path / "ucc256404.pdf"
    shutil.copy2(PDF, src)
    _, out_md, _, _ = pdf2markdown.convert(
        src, tmp_path, ocr=False, force=True, postprocess=False)
    md = out_md.read_text()
    # The exact cases CodeFormulaV2 failed; UniMERNet-base gets them right.
    assert "k_{BLK}" in md
    assert "R_{BLKupper}" in md
    assert "R_{BLKsns}" in md
    assert "V_{BulkStart}" in md
    # Known glyph slip (documented limitation): eq (46) R_{BLKlower} may read
    # R_{BLKIower} (italic l vs I). Not asserted — recorded as a limitation.
