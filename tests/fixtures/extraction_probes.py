"""One-page parametric table probes for mapping the extraction envelope.

`write_grid_probe_pdf` draws a single ruled (or unruled) table where every
cell carries a unique coordinate token (``R03C02``), so extraction fidelity is
mechanically checkable: every token must survive, each row's tokens must stay
in one output row, and no output row may mix tokens from two planted rows.
The knobs (font size, leading, column count, a deliberately narrow wrapping
column, margins, ruling) are the layout parameters most likely to break
table extraction; sweeping them locates the working envelope.
"""

from pathlib import Path
from typing import Dict, List, Optional

from tests.fixtures.representative_docs import _draw_table

DEFAULT_DESC_WORDS = (
    "MODE_SELECT latch enable asserted when the programmed counter threshold "
    "is reached and the divider output is stable"
)


def _wrap(text: str, width_pt: float, font_size: float) -> List[str]:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    lines: List[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if not current or stringWidth(candidate, "Helvetica", font_size) <= width_pt - 6:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def write_grid_probe_pdf(
    path: Path,
    *,
    n_cols: int = 6,
    n_rows: int = 12,
    font_size: float = 8.0,
    leading: Optional[float] = None,
    ruled: bool = True,
    margin: float = 40.0,
    desc_col_width: Optional[float] = None,
    desc_words: Optional[str] = None,
) -> Dict:
    """Write the probe PDF; returns a manifest with ``tokens_by_row``."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    width, height = letter
    usable = width - 2 * margin
    if desc_col_width is not None:
        other = (usable - desc_col_width) / (n_cols - 1)
        col_edges = [margin + i * other for i in range(n_cols)] + [width - margin]
    else:
        step = usable / n_cols
        col_edges = [margin + i * step for i in range(n_cols + 1)]

    if desc_words is None and desc_col_width is not None:
        desc_words = DEFAULT_DESC_WORDS

    header = [f"Col {c}" for c in range(n_cols)]
    tokens_by_row: List[List[str]] = []
    rows: List[List] = [header]
    for r in range(n_rows):
        tokens = [f"R{r:02d}C{c:02d}" for c in range(n_cols)]
        tokens_by_row.append(tokens)
        row: List = list(tokens)
        if desc_words is not None:
            cell_width = col_edges[-1] - col_edges[-2]
            row[-1] = _wrap(f"{tokens[-1]} {desc_words}", cell_width, font_size)
        rows.append(row)

    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, height - margin - 10, "Extraction probe table")
    _draw_table(
        c, col_edges, height - margin - 24, rows,
        font_size=font_size, leading=leading, inner_h_rules=ruled,
    )
    c.showPage()
    c.save()
    return {
        "tokens_by_row": tokens_by_row,
        "n_cols": n_cols,
        "n_rows": n_rows,
        "font_size": font_size,
        "leading": leading,
        "ruled": ruled,
        "margin": margin,
        "desc_col_width": desc_col_width,
    }


def assert_bitfield_table_intact(text: str) -> None:
    """The ruled register-description table's rows survive un-merged with
    their enumerated value lines (see write_register_description_pdf)."""
    from tests.e2e_helpers import find_rows, table_rows

    plain = text.replace("\\_", "_")
    rows = table_rows(plain)
    for bit, name in [("15", "HDR_EN"), ("14:12", "HDR_MODE"),
                      ("11", "HDR_T2_EN"), ("10:8", "HDR_RATIO")]:
        assert find_rows(rows, f" {bit} ", name), f"bit {bit} not paired with {name}"
    assert not any("HDR_EN" in r and "HDR_T2_EN" in r for r in rows), "rows merged"
    assert "linearize / bypass T1 / bypass T2" in plain
    assert "'3'b100 - 2-exposure linearize" in plain


def assert_grid_intact(text: str, manifest: Dict) -> None:
    """Every planted token survived; each planted row's tokens co-occur in
    one output table row; no output row mixes two planted rows."""
    from tests.e2e_helpers import find_rows, table_rows

    rows = table_rows(text)
    tokens_by_row = manifest["tokens_by_row"]
    for tokens in tokens_by_row:
        missing = [t for t in tokens if t not in text]
        assert missing == [], f"tokens lost in conversion: {missing}"
        assert find_rows(rows, *tokens), f"row tokens scattered: {tokens}"
    for i, tokens in enumerate(tokens_by_row[:-1]):
        mixed = find_rows(rows, tokens[0], tokens_by_row[i + 1][0])
        assert mixed == [], f"rows {i} and {i + 1} merged: {mixed}"
