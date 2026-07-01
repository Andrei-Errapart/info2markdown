@AGENTS.md
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single tool, `scripts/pdf2markdown`, that converts a PDF into a clean Markdown
file with external image files. Given `<input.pdf> <output_dir>`, the output dir
gets: a copy of the PDF, a `<stem>.md`, and a `<stem>.images/` directory holding
only the images the `.md` actually references.

There is no build, lint, or test suite — it's a small set of Python scripts run
via a bash wrapper.

## Running

```bash
scripts/pdf2markdown [-f|--force] [--no-ocr] [--no-postprocess] <input.pdf | URL> [output_dir]
```

- The first argument may be a local PDF path **or** an `http(s)` URL. A URL is
  downloaded to a temp file (validated to start with `%PDF`) and then run through
  the same pipeline; the output stem is taken from the URL's last path segment
  (query strings ignored, so `.../ucc256404.pdf?ts=123` → `ucc256404`). With no
  `output_dir`, output goes to the PDF's directory (local input) or the current
  directory (URL input). Quote URLs containing `?`/`&` so the shell doesn't mangle them.
- A first argument that is a **vendor HTML-datasheet URL** is fetched and
  consolidated into one self-contained HTML, then converted by the same docling
  pipeline. Supported vendors: **TI** (`www.ti.com/document-viewer/<part>/datasheet`)
  and **Microchip** (`onlinedocs.microchip.com/g/GUID-…` or `/oxy/…`). New vendors
  are added by implementing `DatasheetSource` in `scripts/datasheet_sources.py`
  and registering it in `SOURCES`.
- First run provisions a virtualenv and **downloads PyTorch + OCR models** — slow
  and network-dependent. Subsequent runs are fast.
- `--no-postprocess` skips the image analysis pass (keeps every image as PNG);
  useful for fast iteration or debugging the docling/split stages in isolation.
- `--no-ocr` disables OCR inside docling (not the post-processing OCR).

## Architecture

The flow is three stages, split across a wrapper + two Python modules. The
conversion/analysis logic was **ported from `/home/andrei/2026/mdoc`** (see its
`vector_postprocessor.py` and `divide_png_md`) — that external repo is the
upstream source of truth if this logic needs updating.

- **`scripts/pdf2markdown`** (bash) — owns the venv lifecycle. The venv lives in
  `${XDG_CACHE_HOME:-~/.cache}/pdf2markdown/venv` (NOT in the repo), so the
  script dir can be read-only / installed system-wide. It re-provisions whenever
  `docling` or any post-processing import is missing, so **adding a dependency to
  `requirements.txt` is picked up automatically** on the next run. It then
  `exec`s `pdf2markdown.py` with the venv's interpreter. Override the location
  with `PDF2MARKDOWN_VENV`, or the bootstrap interpreter with `PYTHON`.

- **`scripts/pdf2markdown.py`** — the engine, run *inside* the venv. Both routes
  drive docling through its **Python API**: `run_docling_pdf` (PDF) and
  `run_docling_html` (HTML), each serialising to base64-embedded Markdown.
  Pipeline: copy the source → convert with docling into a **temp dir** →
  `split_images()` decodes the base64 data-URIs into `<stem>.images/` and rewrites
  the links → exact duplicate images are canonicalized to SHA-256 filenames → hand
  off to post-processing. The intermediate base64 markdown is discarded; it never
  lands in the output dir.
  - The PDF route (`run_docling_pdf`) builds its converter with
    `unimernet_formula.build_pdf_converter`, whose custom pipeline OCRs each
    detected formula region with **UniMERNet-base** instead of docling's
    CodeFormulaV2 (see `scripts/unimernet_formula.py`).
  - The HTML route (`run_docling_html`) uses the docling Python API so it can
    (a) set `fetch_images=True` and (b) plug in a custom Markdown
    serializer that emits `<sub>`/`<sup>` — docling's HTML backend captures
    `<sub>`/`<sup>` formatting but the default serializer drops it, flattening
    prose like `V_comp` to `V comp`. (The PDF backend doesn't capture script
    formatting at all, so PDF prose subscripts can't be recovered.)
  - A final `_normalize_math` pass tidies the per-token-spaced LaTeX the formula
    models emit (`R _ { B L k }` → `R_{BLk}`) inside `$…$`/`$$…$$` spans, keeping
    meaningful spaces (`\, `, `\mu A`, `\max off`). It also unwraps UniMERNet's
    inconsistent `\mathsf{…}`/`\mathrm{…}` font commands to plain italic
    (`_strip_font_commands`, `_FONT_STRIP_CMDS`) and drops the stray thin space
    UniMERNet sometimes emits *inside* a number (`1\,2`→`12`), while keeping
    number→unit thin spaces (`84.4\,\mu H`). Then `_standardize_subscripts`
    uses the equations as a dictionary of real variables and renders any *prose*
    subscript that matches one in the same inline-LaTeX form (`R BLKlower` /
    `R<sub>BLKlower</sub>` → `$R_{BLKlower}$`), so a variable looks identical in
    its equation and in text. Non-equation subscripts stay `<sub>`/plain
    (`SUBSCRIPT_MIN_SUB_LEN` gates short, false-positive-prone names).

- **`scripts/image_postprocess.py`** — runs OCR (RapidOCR, **PP-OCRv5 multilingual**
  model covering English + Japanese) plus structural analysis on *every*
  extracted image and replaces it based on classification:
  - **TABLE** (OpenCV grid-line detection) → reconstruct a Markdown table from
    OCR cell positions, inline it
  - **TEXT** (text-dominant, low color count) → inline the OCR'd text
  - **EQUATION** (a `Equation N.` label just before the image ref) → inline
    `$…$` via **UniMERNet-base** (`unimernet_formula.recognize`), the same
    recognizer the PDF route uses (see `scripts/unimernet_formula.py`). PDF
    *formula regions* are handled during conversion by that module's custom
    docling pipeline (emits `$$…$$`), not here. **Model choice / upgrades:**
    `docs/research/Images_To_Latex.md` surveys the image→LaTeX field (mid-2026)
    and the evaluation pitfalls (benchmark with CDM, not BLEU). UniMERNet-base
    was adopted because it beats CodeFormulaV2 on born-digital PDF equations;
    **PP-FormulaNet-L** is the other specialist worth A/B-ing in this slot.
    Adopting a full engine like MinerU/PaddleOCR-VL would be a pipeline
    replacement, not a model swap.
  - **DIAGRAM** (high edge density / few colors) → trace to `.svg` via `vtracer`,
    but **only for clean, simple line art** (`DIAGRAM_MAX_TEXT_REGIONS`,
    `DIAGRAM_MAX_COLORS`): vtracer deforms complex images and renders text as
    unreadable paths, so text-heavy / colour-complex images stay as PNG instead.
  - **PHOTO** → leave the raster as-is

  Images replaced by inline text/table/LaTeX or by an SVG are deleted as orphans,
  so the images dir only keeps what's still referenced.

- **`scripts/unimernet_formula.py`** — the shared formula OCR for both routes.
  `recognize(img) -> LaTeX` loads **UniMERNet-base** once (downloaded on first
  use, ~1.3 GB). `UniMERNetFormulaModel` is a docling enrichment model that OCRs
  each detected FORMULA crop at ~288 DPI (`images_scale = 4.0`, vs docling's
  120); `UniMERNetPdfPipeline` subclasses `StandardPdfPipeline` to swap it in for
  docling's `CodeFormulaModel`, which then never loads. UniMERNet is installed
  from a **fork** (`Andrei-Errapart/UniMERNet@8dfa160`) that makes it run on a
  modern transformers so it coexists with docling in one venv — stock `unimernet`
  pins transformers 4.42.4, which is irreconcilable with docling.
  - **Equation-number handling (`split_formula_and_number`).** docling's FORMULA
    bbox spans nearly the full page width: formula on the left, the `(N)` number
    far right, a big empty gap between. Fed whole to UniMERNet, the formula is
    squished on resize into the fixed 192×672 frame, dropping trailing
    units/exponents (`µH`, `kHz`, `²`) and emitting long `~` runs for the
    whitespace. So `__call__` splits the crop at the widest interior whitespace
    gap, OCRs the *formula* alone (restores glyph resolution), and reads the
    isolated `(N)` crop separately, appending `\tag{N}` (MinerU's approach —
    numbers come from a separate element, never the formula OCR). `strip_eqno`
    remains as a defensive fallback.
  - **Known limitations:** UniMERNet occasionally misreads a visually ambiguous
    glyph (italic `l`/`I`, `f`/`t`, `M`/`N`) — e.g. UCC256404 p.56 eq (46)
    `R_{BLKlower}`→`R_{BLKIower}`. A lone equation number docling detects as its
    own FORMULA region becomes a stray `$$(N)$$`. Neither is auto-corrected;
    recorded for a future equation-dictionary consensus pass.

- **`scripts/datasheet_sources.py`** — vendor-pluggable HTML datasheet fetchers.
  Defines the `DatasheetSource` interface; two adapters (`TIDocumentViewerSource`
  for TI datasheets, `MicrochipOnlineDocsSource` for Microchip onlinedocs) fetch
  vendor GUID-based HTML pages, inline all images as base64 data-URIs, and produce
  one self-contained `.html` file. This HTML is then fed to docling in
  `pdf2markdown.py`, converging HTML datasheets and PDFs into a single pipeline.
  New vendors are registered in `SOURCES`.
  - **gotcha:** the HTML path uses docling's **Python API** (`run_docling_html`),
    not the CLI, because the CLI doesn't expose the HTML backend's
    `fetch_images` option — without it docling drops every `<img>` and emits
    "Image not available" placeholders. The API sets
    `HTMLBackendOptions(fetch_images=True)` so the inlined data-URI images are
    re-embedded as base64 and `split_images()` extracts them like the PDF route.

## Conventions / gotchas

- **Classification is intentionally conservative** — tunable thresholds are
  module-level constants at the top of `image_postprocess.py`
  (`TABLE_MIN_*`, `TEXT_*`, `DIAGRAM_SCORE_MIN`). An image is only flattened to
  text/table when detection is high-confidence; uncertain cases stay as images.
  Tighten/loosen there, not inline.
- Table *detection* is grid/structure based and language-agnostic; OCR *text*
  fidelity is the limiting factor on dense or merged-cell tables.
- To iterate on classification or OCR without re-running docling, call into the
  module directly with the venv interpreter, e.g.
  `~/.cache/pdf2markdown/venv/bin/python -c 'import image_postprocess; ...'` from
  `scripts/`.
- `requirements.txt` pins opencv/numpy/scipy/Pillow/rapidocr/onnxruntime even
  though docling pulls most of them transitively, because the post-processing
  imports them directly.
