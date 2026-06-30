# HTML Datasheet Support — Design

**Date:** 2026-06-30
**Status:** Approved (design); pending implementation plan
**Component:** `scripts/pdf2markdown` tool

## Context

`pdf2markdown` converts a PDF (local path or, as of commit `f6b47cf`, an
`http(s)` URL) into clean Markdown with external image files. The conversion
goes: download/copy → `docling` (PDF → base64-embedded Markdown) →
`split_images()` (decode data-URIs to `<stem>.images/`) → `deduplicate_images()`
→ `image_postprocess.postprocess()` (OCR + table/text/diagram/photo
classification).

Vendors increasingly publish the **same datasheet as a structured HTML document**
in addition to the PDF. These HTML versions are DITA-derived: real `<table>`
markup, MathML equations, semantic headings/lists, and referenced figures. For
text, tables, lists, and equations the HTML source is expected to yield *higher
fidelity* Markdown than the PDF→OCR route (which rasterizes tables and
equations). The likely weak spot is figures (external, sometimes low-resolution
raster images).

**Goal:** support converting these HTML datasheets through the same tool, via a
**vendor-pluggable** abstraction, with **TI** and **Microchip** as the first two
vendors. A PDF-vs-HTML quality comparison on the same parts is part of
verification.

### Reference examples

| Vendor | PDF | HTML |
|---|---|---|
| TI UCC256404 | `https://www.ti.com/lit/ds/symlink/ucc256404.pdf` | `https://www.ti.com/document-viewer/ucc256404/datasheet` |
| Microchip AVR128DA | `https://ww1.microchip.com/downloads/aemDocuments/documents/MCU08/ProductDocuments/DataSheets/AVR128DA28-32-48-64-Data-Sheet-DS40002183.pdf` | `https://onlinedocs.microchip.com/g/GUID-3EE676DF-490E-41BC-98F0-5774B35DC989` |

Both are listed on the product page, e.g.
`https://www.microchip.com/en-us/product/avr128da48#Documentation`.

## Investigation findings (reverse-engineering)

Both vendors are GUID-based DITA systems but with **different fetch mechanics** —
which is exactly why the abstraction is validated against both.

### TI document-viewer
- The `/document-viewer/<part>/datasheet` page is a JavaScript SPA; the raw HTML
  is a shell + table of contents (no body content).
- The viewer JS (`ticom.dd.min.js`) loads each section with a **`?raw=1`** query
  param: `_retrieve_document_content` → `compute_content_href(href, guid)` →
  `sendAjax(url, {data:{raw:1}})`.
- **Landing page** yields the literature number (`litnum = 'SLUSD90E'`) and the
  **ordered list of section GUIDs** (73 for UCC256404).
- **`<base>/<GUID>?raw=1`** returns a clean DITA fragment
  (`<div class="subsection"><h1>…</h1>…`).
- Concatenated content (~180 KB) contains real `<table>`s (proper
  `<thead>/<th>/<tr>`), ~57 MathML equations, ~20 figures referenced as absolute
  `/ods/images/SLUSD90E/GUID-…-low.gif`.

### Microchip onlinedocs (Oxygen XML WebHelp Responsive)
- `/g/GUID-…` is a short link that **redirects** to
  `/oxy/<root-GUID>-en-US-<ver>/index.html` (the WebHelp deployment dir).
- `index.html` contains the TOC: ordered `href="GUID-XXXX.html"` / `data-id`
  entries (287 topics for AVR128DA).
- Each topic is a **directly-fetchable static XHTML file**
  `<oxybase>/GUID-XXXX.html` (no `?raw=1`), a full page with `<head>`, nav,
  breadcrumb, footer chrome wrapping the topic content. Images are relative.
- Uses MathJax (TeX-MML).

## Architecture

The design hinges on one convergence insight: **a vendor adapter's only job is to
produce one self-contained HTML file with images inlined as base64 data-URIs.**
After that, the existing pipeline runs **unchanged**, because `docling
--image-export-mode embedded` emits base64 data-URIs and `split_images()` already
consumes exactly that. The HTML route and PDF route converge immediately after
the `docling` step.

```
URL ──► find_source(url)
         │
         ├─ vendor source matches ──► source.fetch() ──► self-contained .html ─┐
         │                                                                      │
         ├─ looks like a PDF ───────► download_pdf() ──► .pdf ──────────────────┤
         │                                                                      ▼
         └─ else ► Error                                            convert(input, …)
                                                                       │
   docling (--from pdf|html, embedded) ► split_images ► deduplicate_images ► postprocess
                                                                       │
                                                                       ▼
                                                  <stem>.md + <stem>.images/ + <stem>.{pdf,html}
```

### New module: `scripts/datasheet_sources.py`

```python
class DatasheetSource:                 # interface (ABC/Protocol)
    name: str
    def matches(self, url: str) -> bool: ...
    # Fetch + consolidate into ONE self-contained .html (images inlined as
    # data-URIs) written into work_dir. Returns (html_path, stem).
    def fetch(self, url: str, work_dir: Path) -> tuple[Path, str]: ...

SOURCES = [TIDocumentViewerSource(), MicrochipOnlineDocsSource()]

def find_source(url: str) -> DatasheetSource | None:
    return next((s for s in SOURCES if s.matches(url)), None)
```

Shared helpers in this module (or a small `_http` helper reused from
`pdf2markdown.py`):
- `urlopen` with browser User-Agent + certifi-backed SSL context (the same setup
  `download_pdf` already established).
- A bounded **parallel fetcher** (thread pool, polite concurrency cap ~6,
  per-request timeout) — sequential fetching of 73/287 topics is too slow and
  risks throttling.
- `inline_image(url) -> "data:<mime>;base64,…"`.

### Integration in `scripts/pdf2markdown.py`

`main()`'s URL branch becomes a 3-way decision:

```
if is_url(arg):
    src = find_source(arg)
    if src:
        html, stem = src.fetch(arg, work_dir)   # self-contained .html
        convert(html, output_dir, …)             # docling --from html
    elif _looks_like_pdf(arg):
        pdf = download_pdf(arg, work_dir)
        convert(pdf, output_dir, …)              # existing path
    else:
        raise SystemExit(f"Error: unsupported URL (no datasheet source matched, "
                         f"and not a .pdf): {arg}")
else:
    convert(Path(arg).resolve(), output_dir, …)  # unchanged
```

`convert()` is generalized minimally: accept a `.pdf` **or** `.html` input;
`run_docling()` chooses `--from`/flags by extension (`.html` →
`--from html --to md --image-export-mode embedded`, no `--ocr`; `.pdf` keeps
today's flags). The source artifact is copied into the output dir as
`<stem>.html` or `<stem>.pdf` (analogous to today). Everything after `docling`
is untouched.

## Per-vendor `fetch()` logic

### TIDocumentViewerSource
- **matches:** host `*.ti.com` and path `~ /document-viewer/<part>/datasheet`.
- **fetch:**
  1. GET landing page → `litnum`, ordered GUID list; `stem` = part number from URL.
  2. For each GUID: GET `<base>/<GUID>?raw=1` → DITA fragment.
  3. Inline images: fetch `/ods/images/<litnum>/GUID-…` (try high-res, fall back
     to `-low.gif`) → data-URI; rewrite `src`.
  4. Concatenate fragments → one `<html><body>…</body></html>`.

### MicrochipOnlineDocsSource (Oxygen WebHelp)
- **matches:** host `onlinedocs.microchip.com` (covers `/g/…` and `/oxy/…`).
- **fetch:**
  1. GET entry URL following redirects → resolve `<oxybase>` dir. Derive `stem`
     from the first non-empty of: index `<title>`, the publication
     `<meta name="description">` (observed to start with the part list, e.g.
     "The AVR128DA28/32/48/64(S) microcontrollers…"), or the first topic's `<h1>`;
     sanitize to a filename; fall back to the root GUID only if all are empty.
     (TI's `<title>` is reliable; Microchip's index `<title>` was empty in
     testing, so the fallbacks matter.)
  2. Parse `index.html` TOC → ordered `GUID-XXXX.html` topic list.
  3. For each topic: GET `<oxybase>/GUID-XXXX.html` → extract **only** the topic
     content region (drop nav/breadcrumb/footer/script chrome) with BeautifulSoup.
  4. Inline relative images (resolved against `<oxybase>`) as data-URIs.
  5. Concatenate cleaned bodies → one HTML document.

## Dependencies

- Add `beautifulsoup4` to `scripts/requirements.txt` (parsing TOC + extracting
  topic content; already present transitively via docling, pinned per the repo's
  convention).
- Add `bs4` to the bash wrapper's import probe (line ~29) so existing venvs
  re-provision.
- HTTP remains stdlib `urllib`.

## Error handling

- `SystemExit("Error: …")` (existing fatal idiom) for: unsupported URL, empty /
  parse-failed TOC, total fetch failure.
- Per-section / per-image failures degrade gracefully (skip + `stderr` warning),
  matching `split_images()`'s existing warning style. A single missing figure
  leaves a placeholder rather than aborting the document.

## Testing

- **Unit (no network):**
  - `matches()` positive/negative URLs for both vendors.
  - Stem derivation (TI part number; Microchip title sanitization + fallback).
  - Content-extraction + image-inlining helpers fed **canned HTML fixtures** (a
    saved trimmed TI `?raw=1` fragment and a saved trimmed Microchip topic).
  - Follows the existing `tests/` + `pytest` (`pythonpath = scripts`) setup.
- **Integration (network, opt-in):** `fetch()` against the two real URLs,
  asserting non-trivial consolidated HTML with expected tables/headings. Marked
  `@pytest.mark.slow` / `e2e` so it stays out of the default run.

## Verification (end-to-end, incl. PDF-vs-HTML comparison)

Provision the docling venv once (PyTorch + OCR models), then convert all four
artifacts:

| Part | PDF route | HTML route |
|---|---|---|
| TI UCC256404 | `…/ucc256404.pdf` → `.md` | `…/document-viewer/ucc256404/datasheet` → `.md` |
| Microchip AVR128DA | `…DS40002183.pdf` → `.md` | `onlinedocs…/g/GUID-3EE676DF…` → `.md` |

Compare on: table fidelity (clean `|` Markdown vs OCR reconstruction), heading/
list structure, equations, figure count/quality, overall fidelity. Write up which
route wins where. Also confirm the existing local-PDF and PDF-URL paths still work
(regression).

## Out of scope (YAGNI)

- Generic arbitrary-HTML-page conversion (non-vendor pages).
- Vendors beyond TI and Microchip (the abstraction allows adding them later).
- MathML→LaTeX transformation beyond whatever docling does natively.
- Locale/translated variants (English only).
