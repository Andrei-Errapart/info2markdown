"""Extraction-envelope tests: pin the layout limits of table extraction.

Each swept layout parameter contributes at most two tests: the tightest
variant that still extracts intact (a green guard pinning the working
envelope's edge, so a pipeline or dependency change that shrinks the envelope
fails immediately) and the first variant that breaks (a ``known_defect``
boundary marker asserting the correct behavior). Extend the parametrize lists
to re-measure a ladder.

Measured ladders (2026-07, single-page probes, dependencies as pinned):

- bitfield-table font size: 10/9/8/7/6/5 pt all intact — no failure found
  down to 5 pt.
- bitfield-table leading at 7 pt: 9/8/7.5/7 all intact.
- ruled grid column count: 8/11/14/16 columns all intact.
- narrow wrapping description column: 220/120 pt intact; at 70 pt whole rows
  are silently dropped; 45 pt likewise.
- page margins: 40/24/12/6 pt all intact.
- unruled grid: 6 columns intact; at 11 columns rows are lost/merged.
"""

import pytest

from tests.e2e_helpers import convert_pdf_for_tests
from tests.fixtures.extraction_probes import (
    assert_bitfield_table_intact,
    assert_grid_intact,
    write_grid_probe_pdf,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


def _convert(pdf, out_dir):
    out_md, _ = convert_pdf_for_tests(pdf, out_dir)
    return out_md.read_text(encoding="utf-8")


def _build_grid(path, **kwargs):
    try:
        return write_grid_probe_pdf(path, **kwargs)
    except ModuleNotFoundError as exc:
        pytest.skip(f"fixture dependency unavailable: {exc.name}")


@pytest.mark.parametrize("font_size", [5])
def test_bitfield_font_size_envelope(font_size, artifact_dir):
    """Sweep-verified: intact at 10-5 pt; no failing rung found."""
    from tests.fixtures.known_defect_fixtures import write_register_description_pdf

    pdf = artifact_dir / "probe.pdf"
    try:
        write_register_description_pdf(pdf, ruled_rows=True,
                                       font_size=font_size, leading=font_size + 2)
    except ModuleNotFoundError as exc:
        pytest.skip(f"fixture dependency unavailable: {exc.name}")
    assert_bitfield_table_intact(_convert(pdf, artifact_dir / "out"))


@pytest.mark.parametrize("leading", [7])
def test_bitfield_leading_envelope(leading, artifact_dir):
    """Sweep-verified at 7 pt font: intact down to leading == font size."""
    from tests.fixtures.known_defect_fixtures import write_register_description_pdf

    pdf = artifact_dir / "probe.pdf"
    try:
        write_register_description_pdf(pdf, ruled_rows=True,
                                       font_size=7, leading=leading)
    except ModuleNotFoundError as exc:
        pytest.skip(f"fixture dependency unavailable: {exc.name}")
    assert_bitfield_table_intact(_convert(pdf, artifact_dir / "out"))


@pytest.mark.parametrize("n_cols", [16])
def test_grid_column_count_envelope(n_cols, artifact_dir):
    """Sweep-verified: ruled grids intact at 8/11/14/16 columns."""
    manifest = _build_grid(artifact_dir / "probe.pdf", n_cols=n_cols, font_size=7)
    assert_grid_intact(_convert(artifact_dir / "probe.pdf", artifact_dir / "out"),
                       manifest)


@pytest.mark.parametrize("desc_col_width", [120])
def test_narrow_column_envelope(desc_col_width, artifact_dir):
    """Sweep-verified: a heavily wrapping description column is intact down
    to 120 pt width."""
    manifest = _build_grid(artifact_dir / "probe.pdf", n_cols=5,
                           desc_col_width=desc_col_width)
    assert_grid_intact(_convert(artifact_dir / "probe.pdf", artifact_dir / "out"),
                       manifest)


@pytest.mark.known_defect
@pytest.mark.parametrize("desc_col_width", [70])
def test_narrow_column_boundary(desc_col_width, artifact_dir):
    """First failing rung: at 70 pt the tall wrapped cells make extraction
    silently drop whole table rows."""
    manifest = _build_grid(artifact_dir / "probe.pdf", n_cols=5,
                           desc_col_width=desc_col_width)
    assert_grid_intact(_convert(artifact_dir / "probe.pdf", artifact_dir / "out"),
                       manifest)


@pytest.mark.parametrize("margin", [6])
def test_margin_envelope(margin, artifact_dir):
    """Sweep-verified: intact at 40/24/12/6 pt page margins."""
    manifest = _build_grid(artifact_dir / "probe.pdf", margin=margin)
    assert_grid_intact(_convert(artifact_dir / "probe.pdf", artifact_dir / "out"),
                       manifest)


@pytest.mark.parametrize("n_cols", [6])
def test_unruled_grid_envelope(n_cols, artifact_dir):
    """Sweep-verified: an unruled 6-column grid extracts intact."""
    manifest = _build_grid(artifact_dir / "probe.pdf", n_cols=n_cols, ruled=False)
    assert_grid_intact(_convert(artifact_dir / "probe.pdf", artifact_dir / "out"),
                       manifest)


@pytest.mark.known_defect
@pytest.mark.parametrize("n_cols", [11])
def test_unruled_grid_boundary(n_cols, artifact_dir):
    """First failing rung: at 11 unruled columns rows are lost or merged."""
    manifest = _build_grid(artifact_dir / "probe.pdf", n_cols=n_cols, ruled=False)
    assert_grid_intact(_convert(artifact_dir / "probe.pdf", artifact_dir / "out"),
                       manifest)
