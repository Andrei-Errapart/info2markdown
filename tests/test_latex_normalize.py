"""Normalising the per-token spaced LaTeX docling's formula models emit."""

from pdf2markdown import _normalize_latex, _normalize_math


def test_collapses_token_spaces():
    assert _normalize_latex("V _ { T H } = V _ { C M }") == "V_{TH}=V_{CM}"
    assert _normalize_latex("1 5 . 1 7") == "15.17"
    assert (_normalize_latex(r"R _ { B L k l o w e r } = 1 5 . 1 7 \, M \Omega")
            == r"R_{BLklower}=15.17\,M\Omega")


def test_keeps_spaces_that_terminate_a_command_before_a_letter():
    # \mu A, \max off must keep their space; \times before a digit must not.
    assert _normalize_latex(r"6 2 \mu A") == r"62\mu A"
    assert _normalize_latex(r"t _ { \max o f f }") == r"t_{\max off}"
    assert _normalize_latex(r"\times 1 5 0 m s") == r"\times150ms"
    assert _normalize_latex(r"\alpha \beta") == r"\alpha\beta"


def test_normalize_math_handles_display_and_inline():
    assert _normalize_math("$$V _ { x } = 1$$") == "$$V_{x}=1$$"
    assert _normalize_math("text $V _ { x }$ more") == "text $V_{x}$ more"


def test_normalize_math_leaves_non_math_dollars_alone():
    # currency-style prose, no math tokens -> untouched
    assert _normalize_math("$5 to $10 each") == "$5 to $10 each"
