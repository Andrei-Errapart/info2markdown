"""Builders for representative synthetic documents.

Each builder writes a small multi-page PDF modeled on a real document family
(vendor register references, slide-deck power tutorials, hardware-manual
chapters) and returns a MANIFEST dict of every planted fact, so tests assert
from data instead of duplicating literals. The documents are short but exhibit
the layout features that matter for conversion quality: multi-page tables with
repeated headers, vertically spanning address cells, full-width spanner rows,
page furniture (header logos, doc-code footers, page numbers), special glyphs,
subscripted prose, display formulas, notes under tables, and numbered flows
with lettered sub-items.

All content is fictional (vendor "acmesemi", parts XQ8220/XQR100...).
reportlab is imported lazily so callers can pytest.skip when it is missing.
Only the built-in Type1 fonts are used; Helvetica auto-substitutes Symbol and
ZapfDingbats for glyphs like `` ≤ θ ≅ ✓ − ``. `` ○ `` and `` △ `` are NOT
expressible with built-in fonts (they silently render as a notdef box), which
`_check_plantable` guards against at build time.
"""

from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

from tests.fixtures.known_defect_fixtures import make_register_bit_diagram_png

Cell = Union[str, List[str]]

SPAN = "__SPANNER__"  # marker: row is a single full-width cell


def _check_plantable(*texts: str) -> None:
    """Fail fast if any glyph would silently render as the ZapfDingbats
    notdef box (reportlab raises no error for those)."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.pdfmetrics import getFont

    helv = getFont("Helvetica")
    fonts = [helv] + list(helv.substitutionFonts)
    for text in texts:
        for ch in text:
            if ord(ch) < 128:
                continue
            for font, encoded in pdfmetrics.unicode2T1(ch, fonts):
                if font.fontName == "ZapfDingbats" and encoded == b"n":
                    raise ValueError(
                        f"glyph {ch!r} (U+{ord(ch):04X}) is not expressible "
                        "with the built-in Type1 fonts"
                    )


def _cell_lines(cell: Cell) -> List[str]:
    return [cell] if isinstance(cell, str) else list(cell)


def _draw_table(
    c,
    col_edges: Sequence[float],
    top_y: float,
    rows: List[List[Cell]],
    *,
    font_size: float = 8.0,
    leading: Optional[float] = None,
    header: bool = True,
    inner_h_rules: bool = True,
    col0_blocks: Sequence[Tuple[int, int]] = (),
) -> float:
    """Draw a ruled table; returns the bottom y.

    rows[0] is the header when ``header`` is True (bold, always ruled below).
    A row equal to ``[SPAN, "text"]`` is a full-width spanner: its text is
    drawn once and the row band has no interior vertical rules.
    ``col0_blocks`` are inclusive (start, end) data-row index ranges that share
    column 0: the value is drawn once, vertically centered over the block,
    and no horizontal rules are drawn inside the block (a common
    register-description style).
    """
    if leading is None:
        leading = font_size + 2
    x_left, x_right = col_edges[0], col_edges[-1]
    in_block = set()
    block_start = {}
    for start, end in col0_blocks:
        block_start[start] = end
        for i in range(start, end + 1):
            in_block.add(i)

    heights = []
    for row in rows:
        if row and row[0] == SPAN:
            heights.append(leading + 4)
        else:
            heights.append(max(len(_cell_lines(cell)) for cell in row) * leading + 4)

    y = top_y
    c.setLineWidth(0.4)
    c.line(x_left, y, x_right, y)  # top rule
    for i, row in enumerate(rows):
        row_h = heights[i]
        is_header = header and i == 0
        is_span = row and row[0] == SPAN
        c.setFont("Helvetica-Bold" if is_header else "Helvetica", font_size)

        if is_span:
            c.drawString(x_left + 3, y - leading + 2, row[1])
            c.line(x_left, y - row_h, x_right, y - row_h)
            for x in (x_left, x_right):
                c.line(x, y, x, y - row_h)
            y -= row_h
            continue

        for ci, cell in enumerate(row):
            if ci == 0 and i in in_block and i not in block_start:
                continue  # column 0 drawn once per block
            lines = _cell_lines(cell)
            if ci == 0 and i in block_start:
                # vertically center the shared address over the whole block
                block_h = sum(heights[i:block_start[i] + 1])
                cy = y - block_h / 2 - font_size / 2 + 2
                c.drawString(col_edges[0] + 3, cy, lines[0])
                continue
            for li, line in enumerate(lines):
                c.drawString(col_edges[ci] + 3, y - (li + 1) * leading + 2, line)

        bottom = y - row_h
        rule_below = (
            is_header
            or i == len(rows) - 1
            or (inner_h_rules and not (i in in_block and i != block_start.get(i, -1) and i + 1 in in_block))
        )
        # suppress the rule below a row when the NEXT row is inside the same block
        if i + 1 < len(rows) and i in in_block and i + 1 in in_block and i + 1 not in block_start:
            rule_below = False
        if rule_below:
            c.line(x_left, bottom, x_right, bottom)
        for x in col_edges:
            c.line(x, y, x, bottom)
        y = bottom
    return y


def _draw_runs(c, x: float, y: float, runs, base_font: str = "Helvetica",
               size: float = 10.0) -> float:
    """Draw text runs with sub/superscripts: (text, 0|1|2) = normal|sub|super."""
    for text, kind in runs:
        if kind == 1:
            font, fsize, dy = base_font, size * 0.7, -size * 0.22
        elif kind == 2:
            font, fsize, dy = base_font, size * 0.7, size * 0.35
        else:
            font, fsize, dy = base_font, size, 0.0
        c.setFont(font, fsize)
        c.drawString(x, y + dy, text)
        x += c.stringWidth(text, font, fsize)
    return x


def _pil_png_bytes(draw_fn, size: Tuple[int, int], background=(255, 255, 255)) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, background)
    draw_fn(ImageDraw.Draw(img))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _wordmark_strip_png() -> bytes:
    def draw(d):
        d.rectangle([4, 5, 118, 19], fill=(20, 54, 140))
        d.text((10, 6), "acmesemi", fill=(255, 255, 255))
    return _pil_png_bytes(draw, (157, 25))


def _square_logo_png() -> bytes:
    def draw(d):
        d.rectangle([6, 6, 101, 43], outline=(20, 54, 140), width=3)
        d.text((16, 18), "ACME LOGO", fill=(20, 54, 140))
    return _pil_png_bytes(draw, (108, 50))


def _social_icon_png(seed: int) -> bytes:
    def draw(d):
        d.ellipse([1, 1, 12, 12], fill=(40 + 60 * seed, 60, 120))
    return _pil_png_bytes(draw, (14, 14))


def _chart_png() -> bytes:
    def draw(d):
        d.line([(30, 160), (290, 160)], fill=(0, 0, 0), width=2)   # x axis
        d.line([(30, 10), (30, 160)], fill=(0, 0, 0), width=2)     # y axis
        pts = [(30 + i * 26, 150 - (i * i * 4) % 120) for i in range(11)]
        d.line(pts, fill=(180, 60, 10), width=2)
        d.text((120, 165), "Frequency (Hz)", fill=(0, 0, 0))
        d.text((2, 5), "dB", fill=(0, 0, 0))
    return _pil_png_bytes(draw, (300, 180))


def _image_reader(data: bytes):
    from reportlab.lib.utils import ImageReader
    return ImageReader(BytesIO(data))


# ===========================================================================
# Style 1: register reference (portrait datasheet, table-dominated)
# ===========================================================================

_REG_NAMES = [
    "coarse_integration_time", "fine_integration_time", "analog_gain_code",
    "frame_length_lines", "line_length_pck", "x_addr_start", "x_addr_end",
    "y_addr_start", "y_addr_end", "hdr_control0", "temp_sensor_data",
    "gpio_control", "pll_multiplier", "pll_pre_div", "readout_mode",
    "test_pattern_mode", "data_pedestal", "embedded_data_ctrl",
    "shutter_mode", "black_level_target", "dark_current_trim",
    "row_noise_gain", "adc_bias_ctrl", "col_amp_gain", "vln_dac",
    "boost_ctrl", "sync_mode", "trigger_delay", "flash_ctrl", "otp_status",
]
_FMT_PATTERNS = [
    "dddd dddd dddd dddd", "???? ???? ???? ????",
    "0000 0ddd dddd dddd", "0000 00?? ???? ????",
]


def write_register_reference_pdf(path: Path) -> Dict:
    """A register-reference style datasheet: repeated page-header doc code,
    a key/value overview table with a deliberately overlapping range pair
    (source fidelity), a 4-column register list flowing across a page break
    with heading/notation/header repeated per page, and a wide 11-column
    register-description table whose address cell spans each register block
    with no inner rules (the row-merge trigger)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    doc_code = "AND90311/D"
    confidential = "CONFIDENTIAL AND PROPRIETARY - INTERNAL EVALUATION COPY"
    title = "XQ8220 Register Reference"

    list_rows = []
    for i, name in enumerate(_REG_NAMES):
        dec = 0x3012 + 2 * i
        default = (0x0300 + 7 * i) & 0xFFFF
        list_rows.append({
            "dec_hex": f"{dec} (0x{dec:04X})",
            "name": name,
            "fmt": _FMT_PATTERNS[i % len(_FMT_PATTERNS)],
            "default": f"{default} (0x{default:04X})",
        })

    desc_blocks = [
        {
            "address": "R0x3110",
            "top": ("15:0", "0x0001", "HDR_CONTROL0 (R/W)"),
            "bits": [
                ("15", "X", "Undefined", []),
                ("14:12", "0x0000", "HDR_MODE", [
                    "'3'b0xx - linearize / bypass T1 / bypass T2",
                    "'3'b100 - 2-exposure linearize",
                    "'3'b101 - 2-exposure companding",
                ]),
                ("10:8", "0x0000", "HDR_RATIO", ["Exposure ratio select"]),
            ],
            "trailer": "Contains bitfields for the control of HDR reconstruct",
        },
        {
            "address": "R0x3126",
            "top": ("15:0", "0x0011", "TEMP_CTRL (R/W)"),
            "bits": [
                ("4", "0x0001", "TEMP_START", ["Start a temperature conversion"]),
                ("0", "0x0001", "TEMP_POWER", ["Sensor cell power enable"]),
            ],
            "trailer": "Controls the on-die temperature sensor",
        },
    ]

    address_ranges = [
        ("0x2000-0x2FFF", "Manufacturer-specific registers"),
        ("0x3000-0x33FF", "Sensor core configuration"),
        ("0x3400-0x35FF", "Reserved (undefined)"),
        ("0x3500-0x36FF", "Sequencer RAM access"),
    ]

    manifest = {
        "doc_code": doc_code,
        "confidential_line": "CONFIDENTIAL AND PROPRIETARY",
        "title": title,
        "section_headings": ["Address Space Overview", "Register List",
                             "Register Descriptions"],
        "max_headings": 14,
        "address_ranges": address_ranges,
        "overlap_pair": (address_ranges[2][0], address_ranges[3][0]),
        "register_list_rows": list_rows,
        "page_break_pair": (list_rows[17], list_rows[18]),
        "notation_line": "d = programmable bit, ? = read-only bit",
        "desc_blocks": desc_blocks,
        "verilog_tokens": ["1'b1", "3'b000"],
        "typo": "pixell",
        "urls": {"wrapped": "Patent-Marking.pdf",
                 "plain": "www.acmesemi.com/design/resources/technical-documentation"},
        "page_numbers": ["1", "2", "3", "4"],
    }
    _check_plantable(confidential)

    logo = _pil_png_bytes(
        lambda d: (d.rectangle([2, 2, 67, 21], fill=(20, 54, 140)),
                   d.text((8, 6), "acmesemi", fill=(255, 255, 255))),
        (70, 24),
    )

    c = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter

    def furniture(page: int) -> None:
        c.setFont("Helvetica-Bold", 10)
        c.drawRightString(width - 40, height - 44, doc_code)
        c.setFont("Helvetica", 6.5)
        c.drawCentredString(width / 2, height - 58, confidential)
        c.setFont("Helvetica", 8)
        c.drawCentredString(width / 2, 30, str(page))

    # --- page 1 -----------------------------------------------------------
    furniture(1)
    c.drawImage(_image_reader(logo), 40, height - 52, width=70, height=24)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(40, height - 110, title)
    c.setFont("Helvetica", 9)
    c.drawString(40, height - 140,
                 "This document describes the control registers of the XQ8220 image sensor.")
    c.drawString(40, height - 152,
                 "Identifiers such as x_addr_start and frame_length_lines refer to fields of the")
    c.drawString(40, height - 164,
                 "pixell array readout logic.")
    c.setFont("Helvetica-Bold", 13)
    c.drawString(40, height - 196, "Address Space Overview")
    _draw_table(
        c, [40, 200, 572], height - 210,
        [["Address Range", "Description"]] + [list(pair) for pair in address_ranges],
        font_size=9,
    )
    c.showPage()

    # --- pages 2-3: register list across the page break --------------------
    def list_table_page(page: int, chunk) -> None:
        furniture(page)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(40, height - 90, "Register List")
        c.setFont("Helvetica", 8)
        c.drawString(40, height - 104, "Notation: " + manifest["notation_line"])
        header = ["Register Dec (Hex)", "Name", "Data Format",
                  "Default Value Dec (Hex)"]
        rows = [header] + [[r["dec_hex"], r["name"], r["fmt"], r["default"]]
                           for r in chunk]
        _draw_table(c, [40, 150, 320, 460, 572], height - 118, rows, font_size=8)
        c.showPage()

    list_table_page(2, list_rows[:18])
    list_table_page(3, list_rows[18:])

    # --- page 4: description table + prose + trademark ---------------------
    furniture(4)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(40, height - 90, "Register Descriptions")
    c.setFont("Helvetica", 8)
    c.drawString(40, height - 104, "R/W (Read or Write) bit; RO (Read Only) bit")

    header = ["Register", "Bits", "Default", "Name / Description", "Buf",
              "Bad Fr", "Emb", "Lock", "Sync", "Ord", "Type"]
    rows: List[List[Cell]] = [header]
    blocks: List[Tuple[int, int]] = []
    for block in desc_blocks:
        start = len(rows)
        bits, default, name = block["top"]
        rows.append([block["address"], bits, default, [name], "2", "", "E",
                     "", "", "", "unsigned"])
        for bit, dflt, field, extra in block["bits"]:
            rows.append(["", bit, dflt, [field] + extra, "2", "", "", "", "",
                         "", "unsigned" if field != "Undefined" else ""])
        rows.append(["", "", "", [block["trailer"]], "", "", "", "", "", "", ""])
        blocks.append((start, len(rows) - 1))

    y = _draw_table(
        c, [40, 92, 124, 158, 380, 408, 434, 460, 486, 512, 540, 572],
        height - 118, rows, font_size=6.5, leading=8, col0_blocks=blocks,
    )

    c.setFont("Helvetica", 9)
    c.drawString(40, y - 24,
                 "Set bit 0 to 1'b1 to enable the sequencer; mode values 3'b000 through")
    c.drawString(40, y - 36, "3'b111 select the readout profile.")
    c.drawString(40, y - 60,
                 "XQ8220 and acmesemi are trademarks of Acme Semiconductor. For patent")
    c.drawString(40, y - 72,
                 "information see www.acmesemi.com/site/pdf/Patent-")
    c.drawString(40, y - 84, "Marking.pdf. Additional resources: " + manifest["urls"]["plain"] + ".")
    c.showPage()
    c.save()
    return manifest


# ===========================================================================
# Style 2: slide-deck tutorial (landscape, image-heavy)
# ===========================================================================

def write_slide_tutorial_pdf(path: Path) -> Dict:
    """A slide-deck tutorial: one title per slide, page-furniture wordmark
    strips and logos on every slide, a spec table with empty Min/Typ cells and
    inequality conditions, a thermal table with an attached footnote, a
    transposed product-comparison table with two-dimension package cells, two
    subscripted display formulas, small annotation labels near a chart, and a
    social-media footer."""
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.pdfgen import canvas

    title = "Powering Smart Vision Sensors"
    slide_titles = ["Agenda", "Electrical Specifications",
                    "Choosing the Right Regulator", "Thermal Design Tips"]
    glyph_sentence_winansi = "Derate the load by ±10% above 85°C for reliable operation."
    glyph_sentence_symbol = "Ripple stays ≤ 30 µV while θJA ≅ 42."

    spec_rows = [
        ["Operating Input Voltage", "", "VIN", "2.7", "", "60", "V"],
        ["Output Voltage Accuracy", "VIN ≤ 60 V, 0 mA ≤ IOUT ≤ 500 mA",
         "VOUT", "-2", "", "+2", "%"],
        ["Quiescent Current", "TA ≤ 85°C", "IQ", "", "18", "", "µA"],
    ]
    thermal_rows = [
        ["Thermal Resistance, Junction-to-Air (WLCSP4)", "RθJA", "108", "°C/W"],
        ["Thermal Resistance, Junction-to-Air (SOT23-5)", "RθJA", "218", "°C/W"],
    ]
    thermal_footnote = "- 3. Measured according to the vendor thermal standard."
    comparison_columns = ["XQR100", "XQR200", "XQR300"]
    package_row_cells = {
        "XQR100": "0.65x0.65x0.4 1x1x0.4",
        "XQR200": "0.9x0.9x0.4 1.2x1.2x0.5",
        "XQR300": "1x1x0.4 1.6x1.6x0.6",
    }
    comparison_rows = [
        ["Package X/Y (mm)"] + [package_row_cells[p] for p in comparison_columns],
        ["Max Output Current", "250 mA", "450 mA", "700 mA"],
        ["PSRR at 1 kHz", "92 dB", "92 dB", "93 dB"],
        ["Output Noise", "10 µVRMS", "6.5 µVRMS", "15 µVRMS"],
    ]
    contents_entries = [("Electrical Specifications", "3"),
                        ("Choosing the Right Regulator", "4"),
                        ("Thermal Design Tips", "5")]

    manifest = {
        "title": title,
        "slide_titles": slide_titles,
        "cta": "WATCH WEBINAR",
        "contents_entries": contents_entries,
        "spec_header": ["Parameter", "Test Conditions", "Symbol", "Min",
                        "Typ", "Max", "Unit"],
        "spec_rows": spec_rows,
        "empty_min_typ_row": spec_rows[2],
        "thermal_rows": thermal_rows,
        "thermal_footnote": thermal_footnote,
        "comparison_columns": comparison_columns,
        "package_row_cells": package_row_cells,
        "formula_token_sets": [["t", "on", "off", "≅"], ["θJA", "T", "−", "P"]],
        "annotation_labels_plain": ["Continuous Mode:", "Discontinuous Mode:"],
        "annotation_label_bold": "THERMAL CHARACTERISTICS",
        "footer_left": "Follow us @acmesemi",
        "footer_right": "© 2025 | Public Information",
        "glyph_sentence_winansi": glyph_sentence_winansi,
        "glyph_sentence_symbol": glyph_sentence_symbol,
        "copyright_line": "© Acme Semiconductor 2025. All rights reserved.",
        "furniture": {"sliver_pt": (157, 25), "logo_pt": (108, 50)},
        "max_headings": 12,
    }
    _check_plantable(glyph_sentence_symbol, "≅", "−", "θJA",
                     *[cell for row in spec_rows for cell in row])

    strip = _wordmark_strip_png()
    logo = _square_logo_png()
    icons = [_social_icon_png(i) for i in range(3)]
    chart = _chart_png()

    c = canvas.Canvas(str(path), pagesize=landscape(letter))
    width, height = landscape(letter)

    def furniture() -> None:
        c.drawImage(_image_reader(strip), 36, height - 40, width=157, height=25)
        c.drawImage(_image_reader(logo), width - 144, height - 62, width=108, height=50)
        c.setFont("Helvetica", 8)
        c.drawString(36, 24, manifest["footer_left"] + "   |   " + manifest["footer_right"])
        for i, icon in enumerate(icons):
            c.drawImage(_image_reader(icon), width - 90 + i * 18, 20, width=14, height=14)

    # --- slide 1: cover -----------------------------------------------------
    furniture()
    c.setFont("Helvetica-Bold", 28)
    c.drawCentredString(width / 2, 400, title)
    c.setFont("Helvetica", 14)
    c.drawCentredString(width / 2, 360, "Choosing regulators for image-sensor power trees")
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(width / 2, 300, manifest["cta"])
    c.showPage()

    # --- slide 2: agenda ----------------------------------------------------
    furniture()
    c.setFont("Helvetica-Bold", 20)
    c.drawString(60, height - 100, "Agenda")
    c.setFont("Helvetica", 12)
    y = height - 150
    for entry, page_no in contents_entries:
        c.drawString(80, y, entry)
        c.drawRightString(700, y, page_no)
        y -= 26
    c.showPage()

    # --- slide 3: spec + thermal tables --------------------------------------
    furniture()
    c.setFont("Helvetica-Bold", 20)
    c.drawString(60, height - 100, slide_titles[1])
    y = _draw_table(
        c, [40, 190, 400, 470, 520, 570, 620, 690], height - 130,
        [manifest["spec_header"]] + spec_rows, font_size=9, leading=12,
    )
    y = _draw_table(
        c, [40, 400, 470, 540, 610], y - 30,
        [["Rating", "Symbol", "Value", "Unit"]] + thermal_rows,
        font_size=9, leading=12,
    )
    c.setFont("Helvetica", 7)
    c.drawString(40, y - 12, thermal_footnote)
    c.showPage()

    # --- slide 4: comparison + formula ---------------------------------------
    furniture()
    c.setFont("Helvetica-Bold", 20)
    c.drawString(60, height - 100, slide_titles[2])
    y = _draw_table(
        c, [40, 230, 390, 550, 710], height - 130,
        [["Parameter"] + comparison_columns] + comparison_rows,
        font_size=9, leading=12,
    )
    _draw_runs(c, 240, y - 50, [
        ("D = t", 0), ("on", 1), (" / (t", 0), ("on", 1), (" + t", 0),
        ("off", 1), (") ≅ V", 0), ("OUT", 1), (" / V", 0), ("IN", 1),
    ], size=14)
    c.showPage()

    # --- slide 5: chart + labels + glyphs + formula ---------------------------
    furniture()
    c.setFont("Helvetica-Bold", 20)
    c.drawString(60, height - 100, slide_titles[3])
    c.drawImage(_image_reader(chart), 60, 200, width=300, height=180)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(430, 400, manifest["annotation_label_bold"])
    c.setFont("Helvetica", 9)
    c.drawString(430, 360, manifest["annotation_labels_plain"][0])
    c.drawString(430, 340, manifest["annotation_labels_plain"][1])
    x = _draw_runs(c, 60, 150, [("The output voltage V", 0), ("OUT", 1),
                                (" tracks the reference within the derated range.", 0)],
                   size=11)
    c.setFont("Helvetica", 10)
    c.drawString(60, 128, glyph_sentence_winansi)
    c.drawString(60, 112, glyph_sentence_symbol)
    c.drawString(60, 96, manifest["copyright_line"])
    _draw_runs(c, 430, 250, [
        ("R", 0), ("θJA", 1), (" = (T", 0), ("J", 1), (" − T", 0), ("A", 1),
        (") / P", 0), ("D", 1),
    ], size=14)
    c.showPage()
    c.save()
    return manifest


# ===========================================================================
# Style 3: hardware-manual chapter (portrait, Renesas-like)
# ===========================================================================

def write_hardware_manual_pdf(path: Path) -> Dict:
    """A hardware-manual chapter: numbered heading ladder, per-page header
    logo sliver + doc-code footer, register list with notes, a register
    bit-description block (run-in metadata + bit diagram + packed value
    cells), a procedure table with spanner rows and em-dash empty cells, a
    (1/2)+(2/2) electrical table with repeated caption/header, a wrapped
    merged table header, a transition matrix with dingbat marks, subscripted
    formulas under an [Expressions] label, a numbered flow with lettered
    sub-items, and a CAUTION block with a line-break hyphenated compound."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    footer_code = "R01AA0042EJ0100 Rev.1.00"
    numbered_headings = [
        ("4.4", "Clock Pulse Generator"),
        ("4.4.1", "Startup Flow"),
        ("4.4.2", "Register List"),
        ("4.4.3", "PLL1 Standby Control Register (CPG_PLL1_STBY)"),
        ("4.4.4", "Starting the SSCG"),
        ("4.4.4.1", "Low-Power Transitions"),
        ("4.4.4.1.1", "Clock Transition Matrix"),
    ]
    register_list_rows = [
        ["Reserved", "-", "-", "0000h to 000Fh", "-"],
        ["PLL1 Monitor Register", "CPG_PLL1_MON", "0000_0000h", "0010h", "32"],
        ["PLL1 Standby Control Register", "CPG_PLL1_STBY", "0000_0004h * 1", "0014h", "32"],
        ["SSCG Control Register", "CPG_SSCG_CTL", "0000_8C00h", "0018h", "32"],
    ]
    notes = [
        "Note 1. The initial value is loaded from the mode pins at reset release.",
        "Note 2. Access is limited to 32-bit units.",
    ]
    runin_pairs = [("Access Size :", "32 bits"),
                   ("Offset Address :", "0014h"),
                   ("Initial Value :", "0000_0004h")]
    bit_rows = [
        ["31 to 21", "-", "All 0", "R",
         ["Reserved", "Whenever it is read, 0b is read."]],
        ["18", "SSC_EN_WEN", "0h", "W", ["Write enable for SSC_EN"]],
        ["2", "SSC_EN", "x", "RW",
         ["SSCG on/off setting", "0b: SSCG off 1b: SSCG on"]],
        ["0", "RESETB", "x", "RW",
         ["PLL1 reset setting", "0b: Reset state 1b: Active"]],
    ]
    procedure_rows = [
        [SPAN, "Pre-process"],
        ["1", "CM33", ["Assert the reset of the target units"], "CPG_RST_1 register"],
        [SPAN, "SSCG start sequence"],
        ["2", "CM33", ["Set the SSCG parameters"], "CPG_PLL1_STBY.RESETB = 1b"],
        ["3", "CM33", ["Release the PLL1 reset",
                       "Note : Wait for the lock time before use."], "—"],
        ["4 *1", "CM33", ["Check (polling) the lock status"], "CPG_PLL1_MON.LOCK = 1b"],
    ]
    electrical_header = ["Item", "I/O Type", "Symbol", "Min.", "Typ.",
                        "Max.", "Unit", "Condition"]
    electrical_rows_1 = [
        ["High-level input voltage", "1.8-V I/O", "V IH", "0.8 × VDD", "—",
         "VDD + 0.3", "V", "—"],
        ["Low-level input voltage", "1.8-V I/O", "V IL", "−0.3", "—",
         "0.2 × VDD", "V", "—"],
        ["Input leakage current", "1.8-V I/O", "I I", "−10", "—", "10",
         "µA", "V in = VSS or VDD"],
    ]
    electrical_rows_2 = [
        ["Pull-up resistance", "1.8-V I/O", "R PU", "33", "50", "100", "kΩ", "—"],
        ["Output rise time", "1.8-V I/O", "t r", "—", "1.2", "2.5", "ns",
         "C L = 30 pF"],
        ["Operating temperature", "All", "T A", "−40", "—", "105", "°C", "—"],
    ]
    matrix_rows = [
        ["Module standby", "—", "✓", "Δ *1", "✗"],
        ["Low-frequency", "✓", "—", "Δ *1", "✗"],
        ["Sleep", "✗", "✗", "—", "●"],
        ["Stop", "×", "✗", "✓", "—"],
    ]
    matrix_legend = [
        "✓ : Transition is possible.",
        "Δ : Transition is conditionally possible.",
        "✗ : Transition is not possible.",
        "● : Automatic transition.",
    ]
    boot_list = {
        "items": ["Initialize the peripheral modules (SD0, PFC).",
                  "Read the loader parameters from the boot device:",
                  "Deploy the loader program according to the information obtained in step 2."],
        "subitems": ["(A) Loader program size",
                     "(B) Loader program load address",
                     "(C) Loader program destination address"],
    }

    manifest = {
        "chapter_band": "SECTION 4 SYSTEM CONTROL",
        "numbered_headings": numbered_headings,
        "opening_sentence": "This section describes the clock pulse generator of this LSI.",
        "typo": "recieve",
        "caution": {"label": "CAUTION", "compound": "AWO-OTHERS"},
        "boot_list": boot_list,
        "register_list_header": ["Register Name", "Abbreviation",
                                 "Initial Value", "Offset Address",
                                 "Access Size [bits]"],
        "register_list_rows": register_list_rows,
        "notes": notes,
        "runin_pairs": runin_pairs,
        "bit_header": ["Bit", "Bit Name", "Initial Value", "R/W", "Description"],
        "bit_rows_packed": ["0b: SSCG off 1b: SSCG on", "0b: Reset state 1b: Active"],
        "undefined_footnote": "x: Undefined value",
        "procedure": {
            "spanners": ["Pre-process", "SSCG start sequence"],
            "reg_tokens": ["CPG_PLL1_STBY.RESETB = 1b", "CPG_PLL1_MON.LOCK = 1b"],
            "footnote_step": "4 *1",
        },
        "expressions_label": "[Expressions]",
        "formula_token_sets": [["F", "FVCO", "65536", "×"], ["F", "Fout", "2"]],
        "electrical": {
            "captions": ["Table 4.2  DC Characteristics (1/2)",
                         "Table 4.2  DC Characteristics (2/2)"],
            "header": electrical_header,
            "rows": electrical_rows_1 + electrical_rows_2,
            "minus_cells": ["−0.3", "−10", "−40"],
            "unit_cells": ["µA", "kΩ", "°C"],
            "split_symbols": ["V IH", "I I", "R PU", "T A"],
        },
        "boot_mode_header_joined": "MD_BOOT_2",
        "matrix": {"marks": ["✓", "✗", "●", "Δ", "×", "—"],
                   "legend": matrix_legend},
        "footer_code": "R01AA0042EJ0100",
        "furniture_sliver_pt": (157, 25),
        "max_headings": 16,
    }
    flat_cells = [
        cell for rows in (register_list_rows, electrical_rows_1,
                          electrical_rows_2, matrix_rows)
        for row in rows for cell in row if isinstance(cell, str)
    ]
    _check_plantable(*(flat_cells + matrix_legend))

    strip = _wordmark_strip_png()
    diagram = make_register_bit_diagram_png(
        path.parent, "cpg_pll1_stby_diagram", "CPG_PLL1_STBY (0014h)",
        ["RESETB", "SSC_EN", "SSC_EN_WEN"],
    ).read_bytes()

    c = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter

    def furniture(page: int) -> None:
        c.drawImage(_image_reader(strip), 40, height - 46, width=157, height=25)
        c.setFont("Helvetica", 8)
        c.drawString(40, 28, footer_code)
        c.drawRightString(width - 40, 28, str(page))

    def heading(num_title: Tuple[str, str], y: float, size: float = 11) -> None:
        c.setFont("Helvetica-Bold", size)
        c.drawString(40, y, f"{num_title[0]} {num_title[1]}")

    # --- page 1 -------------------------------------------------------------
    furniture(1)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, height - 96, manifest["chapter_band"])
    heading(numbered_headings[0], height - 126, 12)
    c.setFont("Helvetica", 9)
    c.drawString(40, height - 144, manifest["opening_sentence"])
    c.drawString(40, height - 156, "The CPG can recieve an external reference clock.")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, height - 186, "CAUTION")
    c.setFont("Helvetica", 9)
    c.drawString(40, height - 200,
                 "Do not gate the reference clock while the PLL is locking. Place a protection diode between the AWO-")
    c.drawString(40, height - 212, "OTHERS power domains on the mounting board.")
    heading(numbered_headings[1], height - 244)
    c.setFont("Helvetica", 9)
    y = height - 262
    for i, item in enumerate(boot_list["items"], start=1):
        c.drawString(48, y, f"{i}. {item}")
        y -= 14
        if i == 2:
            for sub in boot_list["subitems"]:
                c.drawString(72, y, sub)
                y -= 14
    c.showPage()

    # --- page 2: register list + notes ---------------------------------------
    furniture(2)
    heading(numbered_headings[2], height - 96)
    y = _draw_table(
        c, [40, 210, 320, 410, 490, 572], height - 112,
        [manifest["register_list_header"]] + register_list_rows, font_size=8,
    )
    c.setFont("Helvetica", 8)
    c.drawString(40, y - 16, notes[0])
    c.drawString(40, y - 28, notes[1])
    c.showPage()

    # --- page 3: bit-description block ---------------------------------------
    furniture(3)
    heading(numbered_headings[3], height - 96)
    c.setFont("Helvetica", 9)
    y = height - 116
    for label, value in runin_pairs:
        c.drawString(40, y, label)
        c.drawString(120, y, value)
        y -= 13
    c.drawImage(_image_reader(diagram), 40, y - 130, width=360, height=120)
    y = _draw_table(
        c, [40, 90, 170, 230, 262, 572], y - 150,
        [manifest["bit_header"]] + bit_rows, font_size=7, leading=9,
    )
    c.setFont("Helvetica", 8)
    c.drawString(40, y - 14, manifest["undefined_footnote"])
    c.showPage()

    # --- page 4: procedure + formulas + electrical (1/2) ----------------------
    furniture(4)
    heading(numbered_headings[4], height - 96)
    y = _draw_table(
        c, [40, 90, 140, 400, 572], height - 112,
        [["Step", "CPU", "Processing", "Remarks"]] + procedure_rows,
        font_size=8,
    )
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, y - 24, manifest["expressions_label"])
    _draw_runs(c, 120, y - 52, [
        ("F", 0), ("FVCO", 1), (" = ((m + k / 65536) × F", 0), ("Fin", 1),
        (") / p", 0),
    ], size=11)
    _draw_runs(c, 120, y - 76, [
        ("F", 0), ("Fout", 1), (" = F", 0), ("FVCO", 1), (" / 2", 0), ("s", 2),
    ], size=11)
    c.setFont("Helvetica", 9)
    c.drawString(40, y - 104, manifest["electrical"]["captions"][0])
    _draw_table(
        c, [40, 165, 235, 285, 335, 385, 440, 480, 572], y - 118,
        [electrical_header] + electrical_rows_1, font_size=6.5, leading=8.5,
    )
    c.showPage()

    # --- page 5: electrical (2/2) + wrapped header + matrix -------------------
    furniture(5)
    c.setFont("Helvetica", 9)
    c.drawString(40, height - 96, manifest["electrical"]["captions"][1])
    y = _draw_table(
        c, [40, 165, 235, 285, 335, 385, 440, 480, 572], height - 110,
        [electrical_header] + electrical_rows_2, font_size=6.5, leading=8.5,
    )
    y = _draw_table(
        c, [40, 120, 200, 320], y - 30,
        [[["MD_", "BOOT_2"], ["MD_", "BOOT_1"], ["Mode"]],
         ["0", "0", "Boot mode 0"],
         ["0", "1", "Boot mode 1"],
         ["1", "x", "Boot mode 3"]],
        font_size=8,
    )
    heading(numbered_headings[5], y - 24, 10)
    heading(numbered_headings[6], y - 42, 9)
    y = _draw_table(
        c, [40, 160, 260, 360, 440, 520], y - 56,
        [["Current Mode", "Module Standby", "Low-Frequency", "Sleep", "Stop"]]
        + matrix_rows,
        font_size=8,
    )
    c.setFont("Helvetica", 8)
    for i, line in enumerate(matrix_legend):
        c.drawString(40, y - 14 - 11 * i, line)
    c.showPage()
    c.save()
    return manifest
