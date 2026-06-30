"""Equation-label detection + LaTeX wrapping (no model / network needed)."""

import re

from image_postprocess import (
    preceded_by_equation_label, wrap_latex, EQUATION_LOOKBACK, IMG_REF_RE,
)

# Mirrors the real TI HTML→docling layout: a standalone "Equation N." paragraph,
# then the image-name paragraph docling emits, then the image. Figures are
# preceded by "Figure N"; cross-references are markdown links "[Equation N](...)".
FIXTURE = """\
## 7.3 Feature Description

Two thresholds V TH and V TL are created.

Equation 1.

GUID-AAA1-low.gif

![Image](x.images/eq1.png)

The VCR voltage is compared with the two thresholds.

Equation 2.

GUID-BBB2-low.gif

![Image](x.images/eq2.png)

## 6.7 Typical Characteristics

Figure 6-1 I HVHigh vs Temperature

GUID-FIG1-low.gif

![Image](x.images/fig1.png)

As the system is shown in [Equation 80](GUID-CCC.html#T49) the design follows.

Equation 80.

GUID-DDD80-low.gif

![Image](x.images/eq80.png)

Some unrelated diagram below.

![Image](x.images/diagram.png)
"""


def _detect_equation_targets(md: str):
    """Replicates postprocess()'s per-ref equation test."""
    out = []
    for m in IMG_REF_RE.finditer(md):
        before = m.string[max(0, m.start() - EQUATION_LOOKBACK):m.start()]
        if preceded_by_equation_label(before):
            out.append(m.group(2))
    return out


def test_detects_exactly_the_labelled_equations():
    targets = _detect_equation_targets(FIXTURE)
    assert targets == ["x.images/eq1.png", "x.images/eq2.png", "x.images/eq80.png"]
    # the figure and the bare diagram are NOT equations
    assert "x.images/fig1.png" not in targets
    assert "x.images/diagram.png" not in targets


def test_label_forms_accepted():
    for label in ["Equation 1.", "Equation 12:", "Eqn 5", "Eq. 7.", "Equation (3)"]:
        assert preceded_by_equation_label(f"text\n\n{label}\n\n")


def test_cross_reference_link_is_not_a_label():
    # the markdown link form must not trigger detection
    assert not preceded_by_equation_label("see [Equation 80](GUID-x.html#T1)\n\n")


def test_figure_label_is_not_an_equation():
    assert not preceded_by_equation_label("Figure 6-1 vs Temperature\n\nGUID-x-low.gif\n\n")


def test_label_too_far_back_is_rejected():
    # an Equation label separated from the image by real prose is not a label-for-this-image
    far = "Equation 9.\n\n" + ("filler sentence. " * 20) + "\n\n"
    assert not preceded_by_equation_label(far)


def test_wrap_latex_inline_vs_display():
    # single-line formula -> inline math
    assert wrap_latex(r"V_{TH}=V_{CM}+\frac{a}{2}") == r"$V_{TH}=V_{CM}+\frac{a}{2}$"
    assert wrap_latex(r"x=1") == "$x=1$"
    # multi-line (LaTeX row break or environment) -> display block
    disp = wrap_latex(r"\begin{aligned} a&=b \\ c&=d \end{aligned}")
    assert disp.startswith("\n$$\n") and disp.endswith("\n$$\n")
