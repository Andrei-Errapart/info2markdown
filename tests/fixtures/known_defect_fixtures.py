"""Builders for synthetic inputs used by the ``known_defect`` test modules.

Each helper reproduces the *shape* of an input that made the converter emit a
defective output on real datasheets (onsemi image-sensor guides, Renesas RZ/V2
hardware-manual chapters): visually distinct but structurally similar register
bit diagrams, oscilloscope screenshots, per-page header logo slivers, and the
PDF table layouts (wide bit-field description tables without inner row rules,
bold standalone captions) that merge cells or misfile captions during
conversion.

Image builders are deterministic. The reportlab PDF builders import reportlab
lazily so callers can ``pytest.skip`` when it is unavailable.
"""

import base64
from pathlib import Path
from typing import List, Sequence, Tuple

from tests.fixtures.generate_duplicate_fixture import make_png

# A valid 1x1 transparent GIF89a (the classic minimal GIF).
TINY_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04"
    b"\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D"
    b"\x01\x00;"
)


def data_uri(data: bytes, fmt: str = "png") -> str:
    return f"data:image/{fmt};base64," + base64.b64encode(data).decode("ascii")


def make_register_bit_diagram_png(
    directory: Path,
    name: str,
    title: str,
    fields: Sequence[str],
) -> Path:
    """A register bit-allocation diagram: framed grid with per-bit cells and
    field names. Two diagrams for different registers share the frame/grid
    structure but differ in every text label — visually distinct content on a
    structurally identical template, like consecutive register figures in a
    hardware manual."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (480, 160), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([4, 4, 475, 155], outline=(0, 0, 0), width=2)
    draw.text((10, 8), title, fill=(0, 0, 0))
    # Bit-cell grid: header band and value band split by a horizontal divider.
    draw.line([(6, 80), (473, 80)], fill=(0, 0, 0), width=1)
    for x in range(10, 471, 30):
        draw.line([(x, 30), (x, 130)], fill=(0, 0, 0), width=1)
    for i, field in enumerate(fields):
        draw.text((14 + (i * 110) % 330, 90 + 20 * ((i * 110) // 330)), field, fill=(0, 0, 0))
    path = directory / f"{name}.png"
    img.save(str(path), format="PNG")
    return path


def make_scope_screenshot_png(directory: Path, name: str) -> Path:
    """An oscilloscope screenshot: dark background, graticule, one bright
    trace. Few unique colours (like a real scope capture), so colour-based
    text heuristics see it as 'ink on a plain ground'."""
    from PIL import Image, ImageDraw
    import math

    img = Image.new("RGB", (640, 480), (10, 12, 16))
    draw = ImageDraw.Draw(img)
    for x in range(0, 640, 64):
        draw.line([(x, 0), (x, 479)], fill=(40, 44, 50), width=1)
    for y in range(0, 480, 48):
        draw.line([(0, y), (639, y)], fill=(40, 44, 50), width=1)
    points = [
        (x, int(240 - 120 * math.sin(x / 40.0)))
        for x in range(0, 640, 4)
    ]
    draw.line(points, fill=(240, 220, 60), width=2)
    path = directory / f"{name}.png"
    img.save(str(path), format="PNG")
    return path


def scope_ocr_regions() -> List[dict]:
    """OCR regions as returned for a real oscilloscope screenshot: many short,
    confidently-read UI captions (vendor logo, trigger status, timebase,
    channel scale) spread over the image."""
    labels = [
        "Tek", "T Trig'd", "M Pos: 12.00ms", "CH1 5.00V", "CH2 2.00V",
        "M 10.0ms", "CH1 / 1.52V", "MD_TRIGGER output", "md_motion",
        "Meas: Freq 50.02Hz", "Pk-Pk 4.96V", "RMS 1.71V", "1>", "2>",
    ]
    regions = []
    for i, text in enumerate(labels):
        x = 20 + (i % 2) * 320
        y = 12 + (i // 2) * 64
        regions.append({"text": text, "bbox": (x, y, 280.0, 26.0), "confidence": 0.92})
    return regions


def make_sliver_png(directory: Path, name: str, page: int) -> Path:
    """A page-header logo sliver (~157x25 px) as cropped from one page: the
    same logo every page, but each page's render differs by a pixel, so the
    files are byte-distinct while being visually identical."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (157, 25), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([4, 5, 118, 19], fill=(20, 54, 140))
    draw.text((8, 6), "RENESAS", fill=(255, 255, 255))
    # Per-page rendering noise: one off-white pixel that moves with the page.
    img.putpixel((130 + (page % 20), 22), (254, 254, 253))
    path = directory / f"{name}.png"
    img.save(str(path), format="PNG")
    return path


# ---------------------------------------------------------------------------
# reportlab PDF builders (import reportlab lazily; callers skip when missing)
# ---------------------------------------------------------------------------

REGISTER_TABLE_ROWS: List[Tuple[str, str, str, str, str, List[str]]] = [
    ("R0x3110", "15", "HDR_EN", "0x1", "RW", ["1: HDR readout enabled."]),
    ("", "14:12", "HDR_MODE", "0x0", "RW", [
        "HDR reconstruction mode select.",
        "'3'b0xx - linearize / bypass T1 / bypass T2",
        "'3'b100 - 2-exposure linearize",
        "'3'b101 - 2-exposure companding",
    ]),
    ("", "11", "HDR_T2_EN", "0x0", "RW", [
        "T2 exposure enable.",
        "0 - bypass T2 readout",
        "1 - enable T2 readout",
    ]),
    ("", "10:8", "HDR_RATIO", "0x2", "RW", ["Exposure ratio select."]),
]


def write_register_description_pdf(path: Path, *, ruled_rows: bool = True) -> None:
    """A wide register-description table where adjacent bit-field rows carry
    multi-line enumerated values — the layout whose rows get merged (and whose
    wrapped value lines get lost) during conversion. ``ruled_rows=False``
    omits the horizontal separators between data rows (only the frame and the
    header rule remain), as many datasheet tables do."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter
    font_size, leading = 7, 9
    # Column left edges; last value is the table's right edge.
    cols = [40, 110, 152, 250, 300, 340, 570]
    headers = ["Register", "Bit", "Name", "Default", "R/W", "Description"]

    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, height - 50, "HDR_CONTROL0 register description")
    top = height - 76
    y = top

    def draw_row(cells: List[List[str]], bold: bool, rule_above: bool) -> None:
        nonlocal y
        row_lines = max(len(lines) for lines in cells)
        row_height = row_lines * leading + 4
        c.setFont("Helvetica-Bold" if bold else "Helvetica", font_size)
        for ci, lines in enumerate(cells):
            for li, line in enumerate(lines):
                c.drawString(cols[ci] + 3, y - (li + 1) * leading + 2, line)
        c.setLineWidth(0.4)
        if rule_above:
            c.line(cols[0], y, cols[-1], y)
        for x in cols:
            c.line(x, y, x, y - row_height)
        y -= row_height

    draw_row([[h] for h in headers], bold=True, rule_above=True)
    for i, (register, bit, name, default, rw, desc) in enumerate(REGISTER_TABLE_ROWS):
        draw_row(
            [[register], [bit], [name], [default], [rw], desc],
            bold=False,
            rule_above=ruled_rows or i == 0,
        )
    c.line(cols[0], y, cols[-1], y)
    c.showPage()
    c.save()


def write_caption_table_pdf(path: Path) -> None:
    """A page with a bold standalone table caption above a small ruled table —
    the layout whose caption gets promoted to a document heading."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter

    c.setFont("Helvetica", 9)
    c.drawString(54, height - 60, "The trigger subsystem supports the modes listed below.")

    c.setFont("Helvetica-Bold", 12)
    c.drawString(54, height - 100, "Table 5. TRIGGER MODES")

    rows = [
        ("Mode", "Source", "Latency"),
        ("Edge", "GPIO0", "2 cycles"),
        ("Level", "GPIO1", "3 cycles"),
        ("Software", "Register write", "1 cycle"),
    ]
    top = height - 116
    leading = 14
    cols = [54, 170, 300, 430]
    for i, row in enumerate(rows):
        c.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 9)
        for ci, cell in enumerate(row):
            c.drawString(cols[ci] + 4, top - (i + 1) * leading + 3, cell)
    c.setLineWidth(0.5)
    bottom = top - len(rows) * leading
    for i in range(len(rows) + 1):
        c.line(cols[0], top - i * leading, cols[-1], top - i * leading)
    for x in cols:
        c.line(x, top, x, bottom)

    c.setFont("Helvetica", 6.5)
    c.drawString(54, bottom - 12, "Note 1. Latency is measured from the trigger edge to readout start.")

    c.setFont("Helvetica", 9)
    c.drawString(54, bottom - 40, "Software triggering is recommended for calibration runs.")
    c.showPage()
    c.save()
