"""Known text-cleanup defects observed converting real datasheets.

Every ``known_defect`` test here feeds a synthetic snippet that reproduces a
defect seen on real conversions (onsemi image-sensor guides, Renesas RZ/V2
hardware-manual chapters) through the Markdown text-cleanup passes and asserts
the CORRECT output. They fail until the converter is fixed; the default suite
auto-skips them (see ``--known-defects`` in conftest).

Deliberately not covered: defects where the conversion stage destroys the
information before post-processing can see it — line-join dehyphenation
(``AWO-OTHERS`` -> ``AWOOTHERS``) and PDF-text-layer space collapse
(``is read`` -> ``isread``); no text-level rule can restore those reliably.
"""

import re

import pytest

from pdf2markdown import (
    _normalize_math,
    _standardize_subscripts,
    clean_markdown_text,
)


def run_text_pipeline(md: str) -> str:
    """The converter's final Markdown text passes, in pipeline order."""
    return _standardize_subscripts(_normalize_math(clean_markdown_text(md)))


PUA_RANGE = ("\ue000", "\uf8ff")


def _pua_chars(text: str) -> list:
    return [ch for ch in text if PUA_RANGE[0] <= ch <= PUA_RANGE[1]]


# ---------------------------------------------------------------------------
# Symbol-font / escape-code glyphs
# ---------------------------------------------------------------------------

@pytest.mark.known_defect
def test_unmapped_pua_glyphs_do_not_survive():
    """Symbol/Wingdings glyphs outside the small mapped set survive into the
    output as Private-Use-Area codepoints (arrows, delta, table check-marks in
    low-power mode tables). No PUA codepoint may reach the final Markdown."""
    md = (
        "Set the clock source \uf0e0 PLL1 path.\n"
        "\n"
        "| Feature | LP0 | LP1 |\n"
        "| --- | --- | --- |\n"
        "| Retention \uf0a1 | \uf0b4 | \uf044 |\n"
    )

    out = run_text_pipeline(md)

    assert _pua_chars(out) == []


@pytest.mark.known_defect
def test_symbol_escape_micro_unit_joins():
    """A ``/C0109`` (micro sign) escape inside a unit must join its unit
    letter: ``2 /C0109 m pixel`` -> ``2 µm pixel``, not ``2 µ m pixel``."""
    md = "The array uses a 2 /C0109 m pixel.\n"

    out = run_text_pipeline(md)

    assert "2 µm pixel" in out
    assert "µ m" not in out


@pytest.mark.known_defect
def test_unmapped_symbol_escapes_do_not_survive():
    """Raw ``/C####`` symbol escapes outside the small mapped set survive
    verbatim. No ``/C####`` escape may reach the final Markdown."""
    md = "Wait for a settling delay of 5 /C0080 s before restart.\n"

    out = run_text_pipeline(md)

    assert re.search(r"/C\d{4}", out) is None


def test_broken_nbsp_mojibake_removed():
    """Green guard: the broken non-breaking-space mojibake ``Â`` seen in
    register descriptions is already stripped; pin that boundary."""
    md = "The upper bits cannot be used.Â The coarse value continues.\n"

    out = run_text_pipeline(md)

    assert "Â" not in out


# ---------------------------------------------------------------------------
# Page furniture and pseudo-headings
# ---------------------------------------------------------------------------

@pytest.mark.known_defect
def test_page_footer_document_code_lines_removed():
    """Per-page footer document-code lines (``R01UH...`` Rev lines in Renesas
    manuals) are repeated page furniture and must not survive as body text."""
    md = (
        "The CPG controls all internal clocks.\n"
        "\n"
        "R01UH1071EJ0120  Rev.1.20\n"
        "\n"
        "Each PLL is configured independently.\n"
        "\n"
        "R01UH1071EJ0120  Rev.1.20\n"
        "\n"
        "The standby controller gates unused domains.\n"
    )

    out = run_text_pipeline(md)

    assert "R01UH1071EJ0120" not in out
    assert "The CPG controls all internal clocks." in out
    assert "Each PLL is configured independently." in out
    assert "The standby controller gates unused domains." in out


@pytest.mark.known_defect
def test_table_caption_heading_demoted():
    """``Table N.`` captions promoted to ``##`` headings must be demoted to
    plain caption text (most captions in the same document stay plain)."""
    md = (
        "The trigger subsystem supports the modes below.\n"
        "\n"
        "## Table 5. TRIGGER MODES\n"
        "\n"
        "| Mode | Source |\n"
        "| --- | --- |\n"
        "| Edge | GPIO0 |\n"
    )

    out = run_text_pipeline(md)

    assert re.search(r"(?m)^#{1,6}\s+Table \d+\.", out) is None
    assert "Table 5. TRIGGER MODES" in out


@pytest.mark.known_defect
def test_note_caution_conditions_headings_demoted():
    """Run-in labels (``CAUTION``, ``NOTE``, ``Note 1. ...``, ``Conditions:``)
    promoted to headings must be demoted to plain/bold labels."""
    md = (
        "## CAUTION\n"
        "\n"
        "Do not change the divider while the PLL is unlocked.\n"
        "\n"
        "## Note 1. The PLL must be locked before switching.\n"
        "\n"
        "## Conditions:\n"
        "\n"
        "VDD = 1.8 V, Ta = 25 degC\n"
    )

    out = run_text_pipeline(md)

    assert re.search(r"(?m)^#{1,6}\s+(?:CAUTION\b|Note \d|Conditions:)", out) is None
    assert "CAUTION" in out
    assert "Note 1. The PLL must be locked before switching." in out
    assert "Conditions:" in out


@pytest.mark.known_defect
def test_figure_label_headings_demoted():
    """Figure/diagram labels and decorative slide text promoted to document
    headings (diagram titles, squashed screenshot-table titles, social-media
    footers) must not survive as headings."""
    md = (
        "## Continuous Mode:\n"
        "\n"
        "The converter switches at a fixed frequency.\n"
        "\n"
        "## THERMALCHARACTERISTICS\n"
        "\n"
        "![table](doc.images/thermal.png)\n"
        "\n"
        "## Follow us @onsemi\n"
    )

    out = run_text_pipeline(md)

    lines = [line for line in out.splitlines() if line.startswith("#")]
    assert lines == [], f"figure labels survived as headings: {lines}"
    assert "Continuous Mode:" in out
    assert "THERMALCHARACTERISTICS" in out
    assert "Follow us @onsemi" in out


# ---------------------------------------------------------------------------
# Figure-text leak
# ---------------------------------------------------------------------------

@pytest.mark.known_defect
def test_interleaved_bare_number_paragraphs_dropped():
    """Pixel-grid indices leaked from readout-order diagrams appear as short
    bare-number paragraphs interleaved with prose (``1``, ``10|``, ``14 15``).
    Blank-line-separated singles must be dropped like long runs are."""
    md = (
        "The readout order is raster scan within each frame.\n"
        "\n"
        "1\n"
        "\n"
        "2\n"
        "\n"
        "10|\n"
        "\n"
        "14 15\n"
        "\n"
        "3\n"
        "\n"
        "The next figure shows the binning order.\n"
    )

    out = run_text_pipeline(md)

    assert "The readout order is raster scan within each frame." in out
    assert "The next figure shows the binning order." in out
    assert re.search(r"(?m)^[0-9][0-9| ]*\|?$", out) is None


# ---------------------------------------------------------------------------
# Token spacing inside identifiers
# ---------------------------------------------------------------------------

@pytest.mark.known_defect
def test_wrapped_identifier_underscore_space_joined():
    """Identifiers wrapped inside PDF table cells acquire a space after the
    underscore (``MD_ BOOT_2``); the joined identifier must be restored."""
    md = (
        "| MD_ BOOT_2 | MD_ BOOT_1 | MD_ BOOT_0 |\n"
        "| --- | --- | --- |\n"
        "| 0 | 1 | 0 |\n"
    )

    out = run_text_pipeline(md)

    assert "MD_BOOT_2" in out
    assert "MD_ BOOT_2" not in out


@pytest.mark.known_defect
def test_split_electrical_symbol_joined_in_table():
    """Electrical symbol names split by subscript layout in spec tables
    (``V DD``) must be rejoined (``VDD``, or an equivalent subscript form)."""
    md = (
        "| Symbol | Parameter | Min. | Unit |\n"
        "| --- | --- | --- | --- |\n"
        "| V DD | Supply voltage | 1.7 | V |\n"
    )

    out = run_text_pipeline(md)

    assert "V DD" not in out
    assert "VDD" in out or "V_{DD}" in out or "V<sub>DD</sub>" in out


@pytest.mark.known_defect
def test_bitvalue_colon_space_restored():
    """Bit-value legends lose the space after the colon (``0b:On``,
    ``1b:The ...``); the space must be restored."""
    md = (
        "| Field | Description |\n"
        "| --- | --- |\n"
        "| WDTRSTB | 0b:On 1b:The WDT reset signal is masked. |\n"
        "\n"
        "0b:The counter keeps running in sleep.\n"
    )

    out = run_text_pipeline(md)

    assert "0b: On" in out
    assert "1b: The" in out
    assert "0b: The counter keeps running in sleep." in out
    assert re.search(r"[01]b:\S", out) is None


# ---------------------------------------------------------------------------
# Links and URLs
# ---------------------------------------------------------------------------

@pytest.mark.known_defect
def test_two_line_url_hyphen_split_rejoined():
    """A URL wrapped at a hyphen turns its tail into a bogus list bullet
    (``.../technical`` newline ``- documentation``); the hyphenated URL text
    must be rejoined."""
    md = (
        "See www.onsemi.com/design/resources/technical\n"
        "- documentation for the register reference.\n"
    )

    out = run_text_pipeline(md)

    assert "technical-documentation" in out
    assert re.search(r"(?m)^- documentation", out) is None


@pytest.mark.known_defect
def test_malformed_link_target_repaired():
    """Link targets emitted with a leading ``%20`` and a collapsed scheme
    (``(%20http:/www...)``) must be repaired to a valid URL."""
    md = "Visit [www.onsemi.com](%20http:/www.onsemi.com) for support.\n"

    out = run_text_pipeline(md)

    assert "(%20" not in out
    assert "http://www.onsemi.com" in out
