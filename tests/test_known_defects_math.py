"""Known formula-handling defects observed converting real datasheets.

The ``known_defect`` tests feed LaTeX shapes the formula OCR actually emits
(observed on PLL and thermal-resistance equations in real datasheets) through
the converter's math-normalization passes and assert the CORRECT output. They
fail until the converter is fixed; the default suite auto-skips them.

Deliberately not covered: OCR glyph misreads (``FVC0`` for ``FVCO``,
``V_{oUT}``, ``t_{-on}``) — there is no deterministic text-level rule that can
correct those without corrupting legitimate formulas; they need model-side
improvements (e.g. an equation-dictionary consensus pass).
"""

from pathlib import Path
from unittest.mock import patch

import pytest

import image_postprocess
from tests.fixtures.generate_duplicate_fixture import make_png
from tests.test_known_defects_cleanup import run_text_pipeline


@pytest.mark.known_defect
def test_upgreek_command_normalized():
    """The formula OCR emits ``upgreek``-style commands (``\\uprho``) that no
    Markdown math renderer accepts; they must be normalized to the standard
    command (``\\rho``)."""
    md = "$$F _ { V C O } = \\uprho \\cdot f _ { I N }$$\n"

    out = run_text_pipeline(md)

    assert "\\uprho" not in out
    assert "\\rho" in out


@pytest.mark.known_defect
def test_single_cell_array_scaffolding_unwrapped():
    """The formula OCR wraps some single equations in a one-cell
    ``\\begin{array}`` environment; the scaffolding must be unwrapped so the
    plain equation remains."""
    md = "$$\\begin{array} { c } { R _ { \\theta J A } = 6 2 } \\end{array}$$\n"

    out = run_text_pipeline(md)

    assert "\\begin{array}" not in out
    assert "R_{\\theta" in out.replace(" ", "")
    assert "62" in out


class _StubOcr:
    def regions(self, image_path):  # pragma: no cover - not reached
        return []


class _StubLatexOcr:
    def to_latex(self, image_path: Path):
        return "V_{OUT} = V_{REF} (1 + R_{1} / R_{2})"


def test_labeled_equation_image_is_decoded(tmp_path: Path):
    """Green guard: an image directly preceded by a standalone
    ``Equation N.`` label is decoded to inline LaTeX; pin that seam."""
    images_dir = tmp_path / "doc.images"
    images_dir.mkdir()
    (images_dir / "eq.png").write_bytes(make_png(120, 24, [(0, 0, 0), (255, 255, 255)]))
    md_path = tmp_path / "doc.md"
    md_path.write_text(
        "Equation 12.\n\n![eq](doc.images/eq.png)\n",
        encoding="utf-8",
    )

    with (
        patch.object(image_postprocess, "Ocr", _StubOcr),
        patch.object(image_postprocess, "LatexOcr", _StubLatexOcr),
    ):
        counts = image_postprocess.postprocess(md_path, "doc.images")

    assert counts["equation"] == 1
    text = md_path.read_text(encoding="utf-8")
    assert "$V_{OUT} = V_{REF} (1 + R_{1} / R_{2})$" in text
    assert "eq.png" not in text
