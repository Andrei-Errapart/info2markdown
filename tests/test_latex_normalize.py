"""Normalising the per-token spaced LaTeX docling's formula models emit."""

from pdf2markdown import _normalize_latex, _normalize_math, _strip_font_commands


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


def test_strip_font_commands_unwraps_mathsf_and_mathrm():
    assert _strip_font_commands(r"\mathsf{V}_{\mathsf{TH}}") == r"V_{TH}"
    assert _strip_font_commands(r"L_{\mathsf{M}}\times L_{\mathrm{R}}") == r"L_{M}\times L_{R}"
    # nested braces inside the wrapped content are preserved
    assert _strip_font_commands(r"\mathsf{R_{LL/SS\_Upper}}") == r"R_{LL/SS\_Upper}"


def test_strip_font_commands_inserts_space_after_word_command():
    # unwrapping must not merge \times into the content -> \timesN
    assert (_strip_font_commands(r"\frac{8\times\mathsf{N_{PS}}^{2}}{\pi^{2}}")
            == r"\frac{8\times N_{PS}^{2}}{\pi^{2}}")
    # a non-command neighbour needs no space
    assert _strip_font_commands(r"=\mathsf{V}") == r"=V"


def test_strip_font_commands_unbalanced_left_as_is():
    assert _strip_font_commands(r"\mathsf{V") == r"\mathsf{V"


def test_normalize_math_strips_mathsf_and_number_thinspace():
    # \mathsf bloat unwrapped, mid-number \, removed, unit \, kept
    assert (_normalize_math(r"$$\mathsf{V}_{\mathsf{TH}}=1\,2$$")
            == r"$$V_{TH}=12$$")
    assert _normalize_math(r"$$x=84.4\,\upmu H$$") == r"$$x=84.4\,\upmu H$$"
    assert _normalize_math(r"$$y=41\,0$$") == r"$$y=410$$"


def test_normalize_math_unwraps_single_cell_array():
    # a lone equation boxed in a one-cell array is scaffolding -> unwrap it
    assert (_normalize_math(r"$$\begin{array} { c } { R _ { \theta J A } = 6 2 } \end{array}$$")
            == r"$$R_{\theta JA}=62$$")
    assert (_normalize_math(r"text $\begin{array}{c}{x=1}\end{array}$ more")
            == r"text $x=1$ more")
    # a genuine multi-row / multi-column array is a real table -> left intact
    assert (_normalize_math(r"$$\begin{array}{cc} a & b \\ c & d \end{array}$$")
            == r"$$\begin{array}{cc}a&b\\c&d\end{array}$$")
