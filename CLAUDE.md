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

- **`scripts/pdf2markdown.py`** — the engine, run *inside* the venv (so it locates
  `docling` next to `sys.executable`). Pipeline: copy the PDF → run `docling`
  with `--image-export-mode embedded` into a **temp dir** → `split_images()`
  decodes the base64 data-URIs into `<stem>.images/` and rewrites the links →
  exact duplicate images are canonicalized to SHA-256 filenames → hand off to
  post-processing. The intermediate base64 markdown is discarded; it never lands
  in the output dir.

- **`scripts/image_postprocess.py`** — runs OCR (RapidOCR, **PP-OCRv5 multilingual**
  model covering English + Japanese) plus structural analysis on *every*
  extracted image and replaces it based on classification:
  - **TABLE** (OpenCV grid-line detection) → reconstruct a Markdown table from
    OCR cell positions, inline it
  - **TEXT** (text-dominant, low color count) → inline the OCR'd text
  - **DIAGRAM** (high edge density / few colors) → trace to `.svg` via `vtracer`
  - **PHOTO** → leave the raster as-is

  Images replaced by inline text/table or by an SVG are deleted as orphans, so
  the images dir only keeps what's still referenced.

- **`scripts/datasheet_sources.py`** — vendor-pluggable HTML datasheet fetchers.
  Defines the `DatasheetSource` interface; two adapters (`TIDocumentViewerSource`
  for TI datasheets, `MicrochipOnlineDocsSource` for Microchip onlinedocs) fetch
  vendor GUID-based HTML pages, inline all images as base64 data-URIs, and produce
  one self-contained `.html` file. This HTML is then fed to docling in
  `pdf2markdown.py`, converging HTML datasheets and PDFs into a single pipeline.
  New vendors are registered in `SOURCES`.

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
