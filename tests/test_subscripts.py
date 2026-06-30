"""Standardising equation-variable subscripts in prose to inline LaTeX."""

import re

from pdf2markdown import _standardize_subscripts


def test_html_sub_upgraded_only_for_equation_variables():
    md = "$$V_{comp}=1$$\n\nThe V<sub>comp</sub> and X<sub>yz</sub> values."
    out = _standardize_subscripts(md)
    assert "$V_{comp}$" in out            # equation variable -> LaTeX
    assert "X<sub>yz</sub>" in out         # not in equations -> kept as <sub>
    assert "V<sub>comp</sub>" not in out


def test_pdf_flat_form_recovered():
    md = "$$R_{BLKlower}=1$$ $$V_{CR}=2$$\n\nSet R BLKlower then read VCR here."
    out = _standardize_subscripts(md)
    assert "$R_{BLKlower}$" in out
    assert "$V_{CR}$" in out
    assert "R BLKlower" not in out and "read VCR" not in out


def test_longest_match_first_no_corruption():
    md = "$$V_{CC}=1$$ $$V_{CCdrop}=2$$\n\nThe VCCdrop and VCC values."
    out = _standardize_subscripts(md)
    assert "$V_{CCdrop}$" in out
    assert "$V_{CC}$" in out
    assert "$V_{CC}$drop" not in out       # not mis-split


def test_single_char_subscripts_excluded():
    md = "$$L_{N}=1$$\n\nThe LN value stays."   # sub 'N' is len 1
    out = _standardize_subscripts(md)
    assert "LN value stays" in out
    assert "$L_{N}$" not in out


def test_existing_math_spans_untouched():
    md = "$$V_{CR}=1$$\n\nInline $I_{FB}$ stays. Prose VCR converts."
    out = _standardize_subscripts(md)
    assert "$$V_{CR}=1$$" in out           # display equation unchanged
    assert "$I_{FB}$" in out               # pre-existing inline span unchanged
    # bare VCR no longer present outside math
    prose = re.sub(r"\$\$.+?\$\$|\$[^$\n]+\$", " ", out, flags=re.S)
    assert "VCR" not in prose


def test_noop_when_no_equations():
    md = "Just prose with VCR and R BLKlower, no equations."
    assert _standardize_subscripts(md) == md
