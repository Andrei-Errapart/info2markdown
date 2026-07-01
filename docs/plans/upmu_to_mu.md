# Plan: Rewrite `\upmu` → `\mu` in normalized LaTeX output

## Context

A code review found that the tool's LaTeX output uses `\upmu` (upright micro sign
µ) throughout equations — in a datasheet conversion, eq 14, 15, 24, 84, … . `\upmu`
comes from the LaTeX `upgreek` package: it needs `\usepackage{upgreek}` in plain
LaTeX and **does not render at all in stock MathJax/KaTeX**. Since this tool emits
Markdown for the web, those equations render broken. UniMERNet (the formula OCR)
emits `\upmu` for µ in unit expressions common in datasheets (µH, µA, µF).

**Fix:** rewrite `\upmu` → `\mu` inside math spans. `\mu` is a standard command that
renders in KaTeX/MathJax everywhere. This was verified independently and matches the
reviewer's recommendation.

Two nuances, both resolved in favor of the fix:
- `\mu` renders *italic* vs `\upmu`'s *upright*. The reviewer accepts this, and it is
  consistent with the codebase's existing choice — `_strip_font_commands` already
  unwraps fonts "to plain italic for one consistent, clean style"
  (`scripts/pdf2markdown.py:347-351`).
- We deliberately do **not** generalize to `\up<greek>` → `\<greek>`: `\upsilon` is a
  real standard command that such a rule would corrupt to `\silon`. Exploration
  confirmed `\upmu` is the only upgreek token that appears in output, so the fix is an
  explicit, extensible substitution, not a pattern rewrite.

## Change

All math-token normalization is centralized in `scripts/pdf2markdown.py`
(lines 325–443), applied once to the whole `.md` at the single call site in
`convert()` (`pdf2markdown.py:496`). The fix lives entirely in that file.

### 1. Add a tunable constant near the existing "tune here" block

Next to `_FONT_STRIP_CMDS` / `_THIN_SPACE_IN_NUMBER_RE` (`scripts/pdf2markdown.py:347-356`),
add a mapping + boundary-safe compiled regex, following the module's existing
comment-and-constant convention:

```python
# upgreek's ``\upmu`` (upright µ, from UniMERNet on datasheet units like µH/µA/µF)
# needs \usepackage{upgreek} and does NOT render in stock MathJax/KaTeX. Rewrite it
# to standard ``\mu`` (renders everywhere; italic here, matching the plain-italic
# style _strip_font_commands already produces). Only \upmu is observed in output;
# add more here if they surface. Do NOT auto-derive \up<x>->\<x> — \upsilon is
# itself a standard command.
_UPGREEK_REWRITE = {r"\upmu": r"\mu"}
_UPGREEK_RE = re.compile(
    "(?:" + "|".join(re.escape(k) for k in _UPGREEK_REWRITE) + r")(?![A-Za-z])"
)
```

The `(?![A-Za-z])` guard keeps the substitution from biting into a longer `\word`
command that merely starts with `\upmu`.

### 2. Apply it inside `norm()` in `_normalize_math`

Add one step to the `norm()` closure (`scripts/pdf2markdown.py:388-393`), e.g. after
`_strip_font_commands`:

```python
    def norm(inner: str) -> str:
        if not re.search(r"[\\_^{}]", inner):
            return inner
        inner = _normalize_latex(inner)
        inner = _strip_font_commands(inner)
        inner = _UPGREEK_RE.sub(lambda m: _UPGREEK_REWRITE[m.group(0)], inner)
        return _THIN_SPACE_IN_NUMBER_RE.sub("", inner)
```

This automatically covers both `$$…$$` (display, line 394) and `$…$` (inline,
lines 395-396) spans. The guard already admits any span containing `\upmu` (it
contains a backslash). Ordering vs the other steps is functionally irrelevant —
`\upmu` and `\mu` are the same token shape, so `_normalize_latex`'s command-spacing
(`\mu H` keeps its space) behaves identically before or after the rewrite.

Scope note: `_normalize_math` only touches math spans, matching where `\upmu`
occurs. Prose is out of scope (and the review only flagged equations).

## Tests

TDD: update/add tests in `tests/test_latex_normalize.py` first, watch fail, then
implement. Tests import private functions by bare name (`pythonpath = scripts` in
`pytest.ini`); style is plain `def test_*` with raw-string asserts, no fixtures.

- **Update the assertion that currently locks in the bug** —
  `tests/test_latex_normalize.py:54` asserts `\upmu` is preserved:
  ```python
  assert _normalize_math(r"$$x=84.4\,\upmu H$$") == r"$$x=84.4\,\upmu H$$"
  ```
  Change the RHS to `r"$$x=84.4\,\mu H$$"` (the unit thin space `\,` is still kept —
  it precedes a command, not a digit).

- **Add a focused test** next to `test_normalize_math_strips_mathsf_and_number_thinspace`,
  covering display + inline + the no-op cases:
  ```python
  def test_normalize_math_rewrites_upmu_to_mu():
      # \upmu (upgreek, doesn't render in KaTeX/MathJax) -> standard \mu
      assert _normalize_math(r"$$C=10\,\upmu F$$") == r"$$C=10\,\mu F$$"
      assert _normalize_math(r"text $I=62\,\upmu A$ more") == r"text $I=62\,\mu A$ more"
      # already-\mu is left untouched
      assert _normalize_math(r"$$62\mu A$$") == r"$$62\mu A$$"
  ```

## Verification

- `pytest tests/test_latex_normalize.py` — the updated + new tests pass; run the full
  fast suite (`pytest -m "not slow"`) to confirm no regressions.
- Quick direct check without re-running docling (uses the cache venv):
  ```bash
  ~/.cache/pdf2markdown/venv/bin/python -c \
    'import pdf2markdown as p; print(p._normalize_math(r"$$x=84.4\,\upmu H$$"))'
  # expect: $$x=84.4\,\mu H$$
  ```
  (run from `scripts/`).
- End-to-end (optional, slow): re-run the converter on the datasheet that produced the
  flagged equations (UCC256404 per the repo notes); normalization runs on every
  conversion, so the regenerated `.md` will contain `\mu`, not `\upmu`. Grep the output
  for `\upmu` to confirm zero remain.
