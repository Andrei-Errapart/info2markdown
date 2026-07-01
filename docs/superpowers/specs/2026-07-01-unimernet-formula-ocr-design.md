# UniMERNet Formula OCR — Design

**Date:** 2026-07-01
**Status:** Approved (design); pending implementation plan
**Component:** `scripts/pdf2markdown` tool

## Context

`pdf2markdown` converts a PDF or vendor HTML datasheet into clean Markdown.
Equations are currently recognised by **docling's `CodeFormulaModel`**
(`docling-project/CodeFormulaV2`, an idefics3 model):

- **PDF route** — docling's `--enrich-formula` rasterizes each detected formula
  region at a fixed 120 DPI (`CodeFormulaModel.images_scale = 1.67`) and OCRs it,
  emitting `$$…$$`.
- **HTML route** — `image_postprocess.LatexOcr` (also CodeFormulaV2) OCRs the
  `EQUATION`-classified figure images, emitting `$…$`.

CodeFormulaV2 systematically **drops/garbles letters in dense subscripts**. On
the UCC256404 datasheet (born-digital PDF), verified failures include
`R_{BLKlower}`→`R_{BKlower}`, `k_{BLK}`→`k_{BK}`, `V_{BulkStart}`→`V_{BukStart}`.
Because it is locked to 120 DPI training resolution, feeding it a higher-DPI crop
does not help (out-of-distribution).

### Why UniMERNet, and the empirical result

**UniMERNet** (Apache-2.0) is a Mathpix-class open formula model. Verified on the
UCC256404 BLK equations (page 56), rendering the born-digital equations at
~288 DPI:

| Eq | Ground truth | CodeFormulaV2 | UniMERNet-base |
|---|---|---|---|
| 44 | `k_{BLK}` | `k_{BK}` ❌ | `k_{BLK}` ✅ |
| 45 | `R_{BLKsns}=\frac{V^2_{IN(nom)}}{P_{BLKsns}}` | L-drop ❌ | ✅ |
| 46 | `R_{BLKlower}` | `R_{BKlower}` ❌ | `R_{BLKIower}` ⚠️ |
| 47 | `R_{BLKupper}=R_{BLKsns}-R_{BLKlower}` | `R_{BKupper}` ❌ | ✅ |
| 48 | `V_{BulkStart}` | `V_{BukStart}` ❌ | ✅ |

**UniMERNet-base: 4/5 perfect. CodeFormulaV2: 0/5.** `base` was chosen over
`small` (equal on clean input, more robust on degraded input — see the "worse
PDFs" concern). UniMERNet's processor normalises any input to a fixed size, so it
can exploit a high-resolution crop that CodeFormulaV2 cannot.

**Goal:** replace CodeFormulaV2 with **UniMERNet-base** on both routes, feeding it
high-DPI crops, while keeping docling's formula *detection*, table handling, and
document assembly.

## Key constraint: the transformers conflict, and its resolution

Stock `unimernet` 0.2.3 **pins `transformers==4.42.4`**. docling needs a much
newer transformers (`rt_detr_v2` layout model + `AutoModelForImageTextToText`,
≥~4.55; production runs 4.57.6). These ranges are **disjoint** — verified that no
single stock transformers runs both (at 4.46.3 both import but both fail at
runtime, for two different reasons: UniMERNet's `CustomMBartDecoder` rejects the
SDPA default; docling's layout checkpoint is an unrecognised `rt_detr_v2`).

**Resolution — the UniMERNet fork.** Use
`https://github.com/Andrei-Errapart/UniMERNet.git` branch `andrei/update`
(commit `8dfa160`, "Support transformers >=4.55 (docling coexistence)"):

- relocates moved imports (`apply_chunking_to_forward` → `transformers.pytorch_utils`,
  `ModelOutput` → `transformers.utils`);
- forces `_attn_implementation="eager"` so the vendored MBart attention classes
  don't hit the SDPA default;
- **replaces HF `generate()` with a self-contained greedy decode loop** that
  threads the legacy tuple KV-cache — version-proof against the transformers
  Cache/generation API churn. Network math is unchanged, so **greedy output is
  byte-for-byte identical** to the 4.42.4 baseline;
- widens the pin to `transformers >= 4.42.4`.

**Verified 2026-07-01:** the fork + docling + UniMERNet-base all run in **one
Python 3.12 venv at transformers 4.57.6**, with LaTeX output byte-identical to the
4.42.4 runs, and docling still converts. **The integration is therefore a clean
single-venv change — no worker process, no second venv.**

## Global Constraints

- **Python 3.12** for the tool venv (current is 3.9, which cannot run UniMERNet).
- UniMERNet installed from the fork, pinned to commit `8dfa160`:
  `unimernet @ git+https://github.com/Andrei-Errapart/UniMERNet.git@8dfa160`.
- Model: **UniMERNet-base** (`wanderkid/unimernet_base`, ~1.3 GB), downloaded on
  first run and cached (like the existing OCR models); never committed.
- **Pin `transformers==4.57.6`** — the version verified to run *both* docling and
  the fork. Leaving it to float risks pip pulling transformers 5.x (allowed by
  docling's `<5.9.0`/`<6.0.0` bounds), which is unverified against the fork.
- Equation output stays **inline LaTeX**, consistent with the existing
  `_normalize_math` / `_standardize_subscripts` conventions (LaTeX-native user).
- No regression to tables, images, dedupe, or the HTML route's non-equation
  handling.

## Architecture (single venv, approach A)

Both routes converge on docling's **Python API** (the HTML route already uses it).
A custom formula-recognition model is injected into docling's PDF pipeline;
UniMERNet runs **in-process**.

### Components

**`scripts/unimernet_formula.py` (new)** — the shared UniMERNet layer:

- `UniMERNetRecognizer` — loads UniMERNet-base once (`UniMERModel.from_config(...)`
  + `FormulaImageEvalProcessor(image_size=[192,672])`), exposes
  `recognize(image: PIL.Image) -> str` (and a batched form) returning normalised
  LaTeX. Model dir resolved from the first-run download cache.
- `UniMERNetFormulaModel(BaseItemAndImageEnrichmentModel)` — docling enrichment
  model. Class attrs: `images_scale` (~4.0 ≈ 288 DPI — the verified resolution,
  a tunable constant), `expansion_factor` (match CodeFormula's 0.18),
  `elements_batch_size` (large, so all of a document's formulas arrive in one
  `__call__`). `is_processable` returns True for `TextItem` with
  `label == DocItemLabel.FORMULA`. `__call__` reads each `el.image`, runs the
  recognizer, sets `el.item.text = latex`, yields the item.
- `UniMERNetPdfPipeline(StandardPdfPipeline)` — overrides `_init_models` to build
  `enrichment_pipe` with `UniMERNetFormulaModel` in place of `CodeFormulaModel`,
  so docling's formula model **never loads**. Preserves `keep_backend` (needs
  `do_formula_enrichment=True` so the PDF backend stays alive for cropping).

**`scripts/pdf2markdown.py`** — PDF route moves from CLI to the docling Python
API:

- `run_docling_pdf(source, out_dir, ocr)` (new / replaces `build_docling_cmd` +
  the CLI path in `run_docling`): builds `PdfPipelineOptions` (`do_ocr=ocr`,
  `do_table_structure=True`, `table_structure_options.mode=ACCURATE`,
  `do_formula_enrichment=True`, `generate_picture_images=True`), constructs a
  `DocumentConverter` with `PdfFormatOption(pipeline_cls=UniMERNetPdfPipeline,
  pipeline_options=...)`, converts, and serialises to embedded-image Markdown
  (`ImageRefMode.EMBEDDED`) — the same shape `split_images()` already consumes.
- `find_docling`/`build_docling_cmd` CLI plumbing removed for PDFs (the wrapper
  still owns the venv). `split_images`, `deduplicate_images`, `_normalize_math`,
  `_standardize_subscripts`, and `convert()`'s post-stages are unchanged.

**`scripts/image_postprocess.py`** — `LatexOcr` (HTML image-equations) switches
from CodeFormulaV2 to `UniMERNetRecognizer`, so **both routes use one model**.

**`scripts/pdf2markdown` (bash wrapper)** — provisions the venv on **Python 3.12**;
the import probe includes `unimernet`; on first run downloads UniMERNet-base via
`snapshot_download('wanderkid/unimernet_base')` into the cache. Re-provisions when
`unimernet`/docling imports are missing (existing behaviour).

**`scripts/requirements.txt`** — add the fork pin; keep the rest.

### Data flow (PDF)

PDF → docling API: layout detects FORMULA regions + tables + text → for each
FORMULA item, `UniMERNetFormulaModel` renders a 288-DPI crop from the live PDF
backend and OCRs it with the fork → LaTeX into `item.text` → serialise to
embedded-image Markdown → `split_images` → `deduplicate_images` →
`image_postprocess.postprocess` → `_normalize_math` → `_standardize_subscripts`.

### Error handling

- A crop that fails OCR leaves the FORMULA item's existing text and logs a
  warning rather than crashing the document.
- Model-download failure is a clear, actionable error at first run.
- The fork is pinned to a commit SHA (not a moving branch) for reproducibility.

## Known limitations

- **Glyph ambiguity (italic `l` vs `I`, etc.).** UniMERNet occasionally misreads a
  visually ambiguous glyph. Verified case: UCC256404 page 56 equation (46),
  `R_{BLKlower}` is recognised as `R_{BLKIower}` — while the *same* token is read
  correctly in equation (47) on the same page. This class of single-glyph
  confusion is **not** auto-corrected in this design (a cross-equation
  majority-vote fix risks conflating genuinely distinct variables). Recorded here
  with its source so a future approach — e.g. equation-dictionary consensus — can
  revisit it.
- Only equations that docling's layout model marks as FORMULA are recognised;
  detection quality is docling's, unchanged by this work.
- `base` model adds ~1.3 GB to first-run provisioning and is ~2× slower per
  formula than `small` on CPU (accepted for robustness).

## Testing

- **Offline unit tests** — `UniMERNetFormulaModel` wiring with a stub recognizer
  (verify `is_processable`, crop→`item.text` write-back, batch handling); the PDF
  option-building helper (correct flags / pipeline class). Existing
  `_normalize_math` / `_standardize_subscripts` tests remain green.
- **Slow e2e (`slow` marker, opt-in)** — convert UCC256404 page 56 and assert the
  BLK equations come out correct (`k_{BLK}`, `R_{BLKsns}`, `R_{BLKupper}`,
  `V_{BulkStart}`), i.e. the exact cases CodeFormulaV2 failed. Document the one
  known-slip case (eq 46) as an accepted xfail/known-limitation, not a hard
  failure.
- **Manual** — one PDF (`ucc256404.pdf`) and one HTML datasheet
  (`.../document-viewer/ucc256404/datasheet`) end-to-end; confirm equations,
  tables, and images all intact and equation quality improved over CodeFormulaV2.
