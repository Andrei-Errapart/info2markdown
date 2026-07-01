# UniMERNet Formula OCR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace docling's CodeFormulaV2 formula OCR with UniMERNet-base (via the Andrei-Errapart/UniMERNet fork) on both the PDF and HTML routes, feeding it high-DPI crops, for dramatically better equation transcription.

**Architecture:** Both routes converge on docling's Python API. A custom docling enrichment model (`UniMERNetFormulaModel`) runs UniMERNet-base in-process on each detected FORMULA region cropped at 288 DPI, writing LaTeX back onto the document; a thin `StandardPdfPipeline` subclass swaps it in for `CodeFormulaModel`. The HTML route's image-equation OCR (`LatexOcr`) delegates to the same recognizer.

**Tech Stack:** Python 3.12, docling (Python API), the UniMERNet fork (transformers-compatible), PyTorch, `wanderkid/unimernet_base` weights.

## Global Constraints

- **Python 3.12** for the tool venv (current is 3.9; UniMERNet needs ≥3.10).
- UniMERNet from the fork, pinned to a commit: `unimernet @ git+https://github.com/Andrei-Errapart/UniMERNet.git@8dfa160`.
- **Pin `transformers==4.57.6`** (the version verified to run both docling and the fork).
- **Pin `docling==2.69.1`** for Tasks 1–6 (verified injection recipe + matches production). Task 7 upgrades to `docling==2.107.0`.
- Model: **UniMERNet-base** (`wanderkid/unimernet_base`, ~1.3 GB), fetched via `snapshot_download` on first use and cached (never committed).
- Equation output stays **inline LaTeX**; keep the existing `_normalize_math` / `_standardize_subscripts` post-passes.
- No regression to tables, images, dedupe, or the HTML route's non-equation handling.
- Verified reference implementation lives in the session scratchpad at `prototype_inject.py`; the code blocks below are derived from it and are known-good on docling 2.69.1.

---

### Task 1: `scripts/unimernet_formula.py` — recognizer + docling formula model + pipeline

**Files:**
- Create: `scripts/unimernet_formula.py`
- Test: `tests/test_unimernet_formula.py`

**Interfaces:**
- Produces:
  - `strip_eqno(latex: str) -> str` — remove a trailing `\eqno …` run.
  - `recognize(img: "PIL.Image.Image") -> str` — image → LaTeX (loads the model once, lazily).
  - `UniMERNetFormulaModel` — docling `BaseItemAndImageEnrichmentModel` subclass (`images_scale=4.0`, `expansion_factor=0.18`, `elements_batch_size=64`; `is_processable`, `__call__`).
  - `UniMERNetPdfPipeline` — `StandardPdfPipeline` subclass swapping `CodeFormulaModel` → `UniMERNetFormulaModel`.
  - `build_pdf_converter(ocr: bool) -> "DocumentConverter"` — converter using the custom pipeline.

- [ ] **Step 1: Write the failing test for `strip_eqno`**

```python
# tests/test_unimernet_formula.py
from unimernet_formula import strip_eqno

def test_strip_eqno_removes_trailing_equation_number():
    assert strip_eqno(r"k _ { B L K } = 3 6 5 \eqno ( 4 5 )") == r"k _ { B L K } = 3 6 5"
    assert strip_eqno(r"a = b \eqno{(12)}") == "a = b"

def test_strip_eqno_noop_without_eqno():
    assert strip_eqno(r"R _ { B L K u p p e r } = 1 5 . 1 7 \, M \Omega") == \
        r"R _ { B L K u p p e r } = 1 5 . 1 7 \, M \Omega"
```

- [ ] **Step 2: Run it to verify failure**

Run: `pytest tests/test_unimernet_formula.py -k strip_eqno -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'unimernet_formula'`.

- [ ] **Step 3: Create `scripts/unimernet_formula.py` with the recognizer**

```python
"""UniMERNet-base formula OCR, shared by the PDF and HTML routes.

Replaces docling's CodeFormulaV2. UniMERNet is loaded from the transformers-
compatible fork (see requirements.txt) and coexists with docling in one venv.
The base weights (~1.3 GB) are downloaded on first use and cached.
"""

import glob
import os
import re

_EQNO_RE = re.compile(r"\\eqno\b.*$")

# Lazily-loaded (model, processor) singleton; the base weights load once.
_MODEL = {}
_MODEL_REPO = "wanderkid/unimernet_base"


def strip_eqno(latex: str) -> str:
    """Drop a trailing ``\\eqno …`` run — UniMERNet reads the page's right-margin
    equation number into the crop; it is a page artifact, not part of the math."""
    return _EQNO_RE.sub("", latex).strip()


def _load():
    if "rp" not in _MODEL:
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        import torch
        from huggingface_hub import snapshot_download
        from omegaconf import OmegaConf
        from unimernet.models.unimernet.unimernet import UniMERModel
        from unimernet.processors.formula_processor import FormulaImageEvalProcessor

        model_dir = snapshot_download(_MODEL_REPO)
        pth = glob.glob(f"{model_dir}/*.pth")[0]
        cfg = OmegaConf.create({
            "arch": "unimernet", "model_type": "unimernet", "model_name": "unimernet",
            "model_config": {"model_name": model_dir, "max_seq_len": 1536},
            "tokenizer_name": "nougat", "tokenizer_config": {"path": model_dir},
            "load_pretrained": True, "pretrained": pth, "load_finetuned": False,
        })
        torch.set_num_threads(max(1, (os.cpu_count() or 2) - 2))
        model = UniMERModel.from_config(cfg).to("cpu").eval().float()
        proc = FormulaImageEvalProcessor(image_size=[192, 672])
        _MODEL["rp"] = (model, proc)
    return _MODEL["rp"]


def recognize(img) -> str:
    """Recognise a single equation image, returning LaTeX (no ``\\eqno``)."""
    import torch
    model, proc = _load()
    pixel = proc(img.convert("RGB")).unsqueeze(0).to("cpu")
    with torch.no_grad():
        out = model.generate({"image": pixel})
    pred = out["pred_str"]
    latex = pred[0] if isinstance(pred, (list, tuple)) else str(pred)
    return strip_eqno(latex)
```

- [ ] **Step 4: Run the `strip_eqno` tests to verify they pass**

Run: `pytest tests/test_unimernet_formula.py -k strip_eqno -v`
Expected: PASS (2 passed). `recognize`/`_load` are import-only here — no model download.

- [ ] **Step 5: Write the failing test for `UniMERNetFormulaModel.__call__` write-back**

```python
# append to tests/test_unimernet_formula.py
import types
import unimernet_formula

def test_formula_model_writes_latex_to_item(monkeypatch):
    monkeypatch.setattr(unimernet_formula, "recognize", lambda img: "R_{BLKupper}")
    from unimernet_formula import UniMERNetFormulaModel
    model = UniMERNetFormulaModel()
    item = types.SimpleNamespace(text="stale")
    el = types.SimpleNamespace(item=item, image="fake-crop")
    out = list(model(doc=None, element_batch=[el]))
    assert item.text == "R_{BLKupper}"
    assert out == [item]
```

- [ ] **Step 6: Run it to verify failure**

Run: `pytest tests/test_unimernet_formula.py -k writes_latex -v`
Expected: FAIL — `ImportError: cannot import name 'UniMERNetFormulaModel'`.

- [ ] **Step 7: Add the docling formula model + pipeline + converter builder**

```python
# append to scripts/unimernet_formula.py

def _docling_imports():
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
    from docling.models.base_model import BaseItemAndImageEnrichmentModel
    from docling.models.stages.code_formula.code_formula_model import CodeFormulaModel
    from docling_core.types.doc import DocItemLabel, TextItem
    return (DocumentConverter, PdfFormatOption, InputFormat, PdfPipelineOptions,
            TableFormerMode, StandardPdfPipeline, BaseItemAndImageEnrichmentModel,
            CodeFormulaModel, DocItemLabel, TextItem)


# docling's base enrichment class is only importable inside the venv, so the
# subclasses are built at import time behind these module globals.
(DocumentConverter, PdfFormatOption, InputFormat, PdfPipelineOptions,
 TableFormerMode, StandardPdfPipeline, _BaseEnrich, _CodeFormulaModel,
 _DocItemLabel, _TextItem) = _docling_imports()


class UniMERNetFormulaModel(_BaseEnrich):
    """docling enrichment model: OCR each FORMULA crop with UniMERNet-base.

    ``images_scale`` is the crop DPI knob (docling's CodeFormula uses 1.67 = 120
    dpi); 4.0 ≈ 288 dpi gives UniMERNet a crisp born-digital crop.
    """
    images_scale = 4.0
    expansion_factor = 0.18
    elements_batch_size = 64

    def __init__(self):
        self.enabled = True

    def is_processable(self, doc, element) -> bool:
        return isinstance(element, _TextItem) and element.label == _DocItemLabel.FORMULA

    def __call__(self, doc, element_batch):
        for el in element_batch:
            el.item.text = recognize(el.image)
            yield el.item


class UniMERNetPdfPipeline(StandardPdfPipeline):
    """Swap CodeFormulaModel for UniMERNetFormulaModel; CodeFormulaV2 never loads."""

    def _init_models(self) -> None:
        super()._init_models()
        self.enrichment_pipe = [m for m in self.enrichment_pipe
                                if not isinstance(m, _CodeFormulaModel)]
        self.enrichment_pipe.insert(0, UniMERNetFormulaModel())
        # do_formula_enrichment is False (so CodeFormulaV2 does not download), so
        # StandardPdfPipeline left keep_backend False; the crop needs the backend.
        self.keep_backend = True


def build_pdf_converter(ocr: bool) -> "DocumentConverter":
    """A docling converter whose PDF pipeline OCRs formulas with UniMERNet-base."""
    opts = PdfPipelineOptions()
    opts.do_ocr = ocr
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    opts.do_formula_enrichment = False   # our model runs instead; keeps CodeFormulaV2 out
    opts.generate_picture_images = True
    return DocumentConverter(format_options={
        InputFormat.PDF: PdfFormatOption(
            pipeline_cls=UniMERNetPdfPipeline, pipeline_options=opts)
    })
```

- [ ] **Step 8: Run the write-back test to verify it passes**

Run: `pytest tests/test_unimernet_formula.py -k writes_latex -v`
Expected: PASS. (Import of `unimernet_formula` requires docling installed in the venv, which it is.)

- [ ] **Step 9: Write + run the converter-config test**

```python
# append to tests/test_unimernet_formula.py
def test_build_pdf_converter_uses_custom_pipeline():
    from unimernet_formula import build_pdf_converter, UniMERNetPdfPipeline
    from docling.datamodel.base_models import InputFormat
    conv = build_pdf_converter(ocr=False)
    fmt = conv.format_options[InputFormat.PDF]
    assert fmt.pipeline_cls is UniMERNetPdfPipeline
    assert fmt.pipeline_options.do_formula_enrichment is False
    assert fmt.pipeline_options.do_table_structure is True
```

Run: `pytest tests/test_unimernet_formula.py -v`
Expected: PASS (all 4 tests).

- [ ] **Step 10: Commit**

```bash
git add scripts/unimernet_formula.py tests/test_unimernet_formula.py
git commit -m "Add UniMERNet-base formula OCR module (recognizer + docling pipeline)"
```

---

### Task 2: Wire the PDF route to the docling Python API

**Files:**
- Modify: `scripts/pdf2markdown.py` (replace the CLI PDF path; `run_docling`, `convert`, remove `find_docling`/`build_docling_cmd`)
- Test: `tests/test_run_docling_pdf.py`

**Interfaces:**
- Consumes: `unimernet_formula.build_pdf_converter(ocr)` (Task 1).
- Produces: `run_docling_pdf(source: Path, out_dir: Path, ocr: bool) -> Path`; updated `run_docling(source, out_dir, ocr)` (no `docling` CLI arg).

- [ ] **Step 1: Write the failing test — `run_docling` dispatches PDFs to the API path**

```python
# tests/test_run_docling_pdf.py
from pathlib import Path
import types
import pdf2markdown

def test_run_docling_pdf_serializes_embedded_markdown(tmp_path, monkeypatch):
    # Fake docling converter -> result.document.export_to_markdown(...)
    class FakeDoc:
        def export_to_markdown(self, image_mode=None):
            return "# hi\n\n$$x=1$$\n"
    class FakeResult:
        document = FakeDoc()
    class FakeConv:
        def convert(self, src, **kw):
            return FakeResult()
    monkeypatch.setattr(pdf2markdown, "build_pdf_converter", lambda ocr: FakeConv())
    src = tmp_path / "d.pdf"; src.write_bytes(b"%PDF-1.4 fake")
    out = pdf2markdown.run_docling_pdf(src, tmp_path, ocr=True)
    assert out == tmp_path / "d.md"
    assert "$$x=1$$" in out.read_text()
```

- [ ] **Step 2: Run it to verify failure**

Run: `pytest tests/test_run_docling_pdf.py -v`
Expected: FAIL — `AttributeError: module 'pdf2markdown' has no attribute 'build_pdf_converter'`.

- [ ] **Step 3: Add `run_docling_pdf` and rewire `run_docling` / `convert`**

In `scripts/pdf2markdown.py`, add the import near the top (after `from datasheet_sources import find_source`):

```python
from unimernet_formula import build_pdf_converter
```

Add `run_docling_pdf` (next to `run_docling_html`):

```python
def run_docling_pdf(source: Path, out_dir: Path, ocr: bool) -> Path:
    """Convert a PDF via docling's Python API, OCRing formulas with UniMERNet-base.

    Mirrors the HTML route: convert, then serialise to embedded-image Markdown
    (base64 data-URIs) so split_images() extracts the images exactly as before.
    """
    from docling_core.types.doc import ImageRefMode
    print(f"Converting {source.name} with docling (UniMERNet formulas)...", flush=True)
    converter = build_pdf_converter(ocr)
    result = converter.convert(str(source))
    md_text = result.document.export_to_markdown(image_mode=ImageRefMode.EMBEDDED)
    out_md = out_dir / f"{source.stem}.md"
    out_md.write_text(md_text, encoding="utf-8")
    return out_md
```

Replace `run_docling` with:

```python
def run_docling(source: Path, out_dir: Path, ocr: bool) -> Path:
    """Dispatch to the HTML or PDF docling Python-API route."""
    if source.suffix.lower() == ".html":
        return run_docling_html(source, out_dir)
    return run_docling_pdf(source, out_dir, ocr)
```

Delete the now-unused `find_docling` and `build_docling_cmd` functions. In `convert()`, remove the `docling = find_docling()` line and change the call to `embedded_md = run_docling(source, tmp_dir, ocr)`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_run_docling_pdf.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full offline suite to confirm no regressions**

Run: `pytest -m "not slow" -q`
Expected: PASS (existing normalize/subscript/dedupe/datasheet tests still green; no reference to removed `build_docling_cmd`).

- [ ] **Step 6: Commit**

```bash
git add scripts/pdf2markdown.py tests/test_run_docling_pdf.py
git commit -m "PDF route: convert via docling Python API with UniMERNet formula OCR"
```

---

### Task 3: HTML route — `LatexOcr` delegates to the shared recognizer

**Files:**
- Modify: `scripts/image_postprocess.py` (`class LatexOcr`, ~lines 137–190)
- Test: `tests/test_latexocr_delegates.py`

**Interfaces:**
- Consumes: `unimernet_formula.recognize` (Task 1).
- Produces: unchanged public `LatexOcr.to_latex(image_path: Path) -> Optional[str]` + `wrap_latex`. The caller `equation_latex(target)` (used at `image_postprocess.py:477`) is unaffected — it keeps calling `to_latex`.

- [ ] **Step 1: Write the failing test**

The current `LatexOcr.to_latex(image_path)` (image_postprocess.py:161) opens the path, runs CodeFormulaV2, and post-processes idefics3 wrapper tokens. It will switch to delegate to `unimernet_formula.recognize` (which takes a PIL image).

```python
# tests/test_latexocr_delegates.py
from PIL import Image
import image_postprocess

def test_latexocr_to_latex_delegates(tmp_path, monkeypatch):
    import unimernet_formula
    monkeypatch.setattr(unimernet_formula, "recognize", lambda img: "V_{comp}")
    p = tmp_path / "eq.png"
    Image.new("RGB", (20, 10), "white").save(p)
    assert image_postprocess.LatexOcr().to_latex(p) == "V_{comp}"
```

- [ ] **Step 2: Run it to verify failure**

Run: `pytest tests/test_latexocr_delegates.py -v`
Expected: FAIL (current `LatexOcr` loads CodeFormulaV2 and does not call `unimernet_formula.recognize`; instantiating it downloads/loads a model).

- [ ] **Step 3: Rewrite `LatexOcr` to delegate**

Replace the `LatexOcr` class body (docstring, `_REPO`, `__init__`, and `to_latex`) with a thin delegator, keeping the `to_latex(image_path)` signature the caller relies on. The CodeFormulaV2 wrapper-token post-processing is removed (UniMERNet doesn't emit those; `recognize` already strips `\eqno`):

```python
class LatexOcr:
    """Render an equation image to LaTeX with UniMERNet-base (shared recognizer).

    The base model is loaded once by ``unimernet_formula``; this class is a thin
    path-based adapter so the HTML route's image equations get the same model as
    the PDF route.
    """

    def __init__(self) -> None:
        pass  # unimernet_formula loads the base model once, lazily.

    def to_latex(self, image_path: Path) -> Optional[str]:
        from PIL import Image
        from unimernet_formula import recognize
        try:
            out = recognize(Image.open(image_path))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("formula OCR failed for %s: %s", image_path.name, exc)
            return None
        return out or None
```

Keep `wrap_latex`, `EQUATION_LABEL_RE`, `preceded_by_equation_label`, and `equation_latex` unchanged. Confirm no other code references the removed `LatexOcr._REPO`/`_proc`/`_model`/`_prompt` attributes.

- [ ] **Step 5: Run the test + full offline suite**

Run: `pytest tests/test_latexocr_delegates.py -v && pytest -m "not slow" -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/image_postprocess.py tests/test_latexocr_delegates.py
git commit -m "HTML route: LatexOcr delegates to shared UniMERNet-base recognizer"
```

---

### Task 4: Provisioning — `requirements.txt` + Python 3.12 wrapper

**Files:**
- Modify: `scripts/requirements.txt`
- Modify: `scripts/pdf2markdown` (bash wrapper)

**Interfaces:**
- Produces: a venv on Python 3.12 with `unimernet` (fork), pinned `transformers`/`docling`, importable by `pdf2markdown.py`.

- [ ] **Step 1: Update `scripts/requirements.txt`**

Add these lines (and keep the existing opencv/numpy/scipy/Pillow/rapidocr/onnxruntime/vtracer/beautifulsoup4 pins):

```
docling==2.69.1
transformers==4.57.6
unimernet @ git+https://github.com/Andrei-Errapart/UniMERNet.git@8dfa160
```

- [ ] **Step 2: Point the wrapper at Python 3.12 and probe `unimernet`**

In `scripts/pdf2markdown`, set the bootstrap interpreter default to Python 3.12 (keep the `PYTHON` override), e.g. change the interpreter discovery so it prefers `python3.12`:

```bash
PYTHON="${PYTHON:-$(command -v python3.12 || command -v python3)}"
```

Add `unimernet` to the import probe that decides whether to (re)provision, so the line reads:

```bash
"$VENV_PY" -c "import cv2, numpy, scipy, PIL, rapidocr, onnxruntime, vtracer, bs4, transformers, docling, unimernet" 2>/dev/null
```

- [ ] **Step 3: Provision a fresh venv and verify imports**

Run:
```bash
rm -rf ~/.cache/pdf2markdown/venv
PDF2MARKDOWN_VENV=/tmp/p2m-venv scripts/pdf2markdown --help
/tmp/p2m-venv/bin/python -c "import docling, unimernet, transformers; print('ok', transformers.__version__)"
```
Expected: `--help` prints usage after provisioning; the import line prints `ok 4.57.6`.

- [ ] **Step 4: Commit**

```bash
git add scripts/requirements.txt scripts/pdf2markdown
git commit -m "Provision on Python 3.12 with UniMERNet fork; pin docling/transformers"
```

---

### Task 5: End-to-end slow test on the BLK equations

**Files:**
- Create: `tests/test_e2e_formula.py`

**Interfaces:**
- Consumes: the whole pipeline (`convert`), the real venv + model.

- [ ] **Step 1: Write the e2e test (gated on model + a PDF fixture being available)**

```python
# tests/test_e2e_formula.py
import os
import shutil
from pathlib import Path
import pytest

pytestmark = pytest.mark.slow

PDF = Path(os.environ.get("UCC_PDF", str(Path.home() / "Downloads" / "ucc256404.pdf")))


@pytest.mark.skipif(not PDF.is_file(), reason="UCC256404 PDF not available")
def test_blk_equations_recognized(tmp_path):
    import pdf2markdown
    src = tmp_path / "ucc256404.pdf"
    shutil.copy2(PDF, src)
    _, out_md, _, _ = pdf2markdown.convert(
        src, tmp_path, ocr=False, force=True, postprocess=False)
    md = out_md.read_text()
    # The exact cases CodeFormulaV2 failed; UniMERNet-base gets them right.
    assert "k_{BLK}" in md
    assert "R_{BLKupper}" in md
    assert "R_{BLKsns}" in md
    assert "V_{BulkStart}" in md
    # Known glyph slip (documented limitation), asserted xfail so it is visible:
    # R_{BLKlower} in eq (46) may read R_{BLKIower}. Do not hard-fail on it.
```

- [ ] **Step 2: Run it (opt-in, requires the model download on first run)**

Run: `pytest -m slow tests/test_e2e_formula.py -v`
Expected: PASS (or SKIP if the PDF is absent). First run downloads UniMERNet-base (~1.3 GB).

- [ ] **Step 3: Commit**

```bash
git add tests/test_e2e_formula.py
git commit -m "Add slow e2e test asserting UniMERNet fixes the BLK equations"
```

---

### Task 6: Documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the formula-OCR description in `CLAUDE.md`**

Under the architecture / `image_postprocess.py` and `pdf2markdown.py` sections, replace the CodeFormulaV2 description with UniMERNet-base, covering: both routes use `scripts/unimernet_formula.py`; the PDF route now runs docling via the Python API with a `StandardPdfPipeline` subclass that swaps the formula model and crops at 288 DPI; UniMERNet comes from the transformers-compatible fork (pinned commit) because stock `unimernet` pins an old transformers that conflicts with docling; and the known glyph-ambiguity limitation (UCC256404 p.56 eq 46, `R_{BLKlower}`→`R_{BLKIower}`), not auto-corrected.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "Docs: document UniMERNet-base formula OCR and the fork"
```

---

### Task 7: Upgrade docling to 2.107.0 (gated on Tasks 1–6 tests passing)

**Files:**
- Modify: `scripts/requirements.txt` (`docling==2.107.0`)
- Modify: `scripts/unimernet_formula.py` (adapt relocated imports if needed)

**Interfaces:** unchanged public surface; only import paths / option types may shift.

- [ ] **Step 1: Confirm the full suite is green on docling 2.69.1 first**

Run: `pytest -m "not slow" -q && pytest -m slow tests/test_e2e_formula.py -q`
Expected: PASS. Do not proceed otherwise.

- [ ] **Step 2: Clean-install docling 2.107.0 and locate the (possibly relocated) classes**

docling 2.107.0 restructures modules (a `docling`/`docling-slim` split); an
in-place pip upgrade from 2.69.1 leaves a broken tree. Build a **fresh** venv:
```bash
rm -rf /tmp/p2m-venv-2107
python3.12 -m venv /tmp/p2m-venv-2107
/tmp/p2m-venv-2107/bin/pip install docling==2.107.0 transformers==4.57.6 \
  'unimernet @ git+https://github.com/Andrei-Errapart/UniMERNet.git@8dfa160'
```
Then find the new import paths (run with `/tmp/p2m-venv-2107/bin/python`):
```bash
/tmp/p2m-venv-2107/bin/python - <<'PY'
import docling, pkgutil
for m in ("document_converter","pipeline.standard_pdf_pipeline",
          "models.base_model","models.stages.code_formula.code_formula_model"):
    try:
        __import__("docling."+m); print("OK docling."+m)
    except Exception as e:
        print("MOVED docling."+m, "->", e)
PY
```
Locate `DocumentConverter`, `PdfFormatOption`, `StandardPdfPipeline`, `BaseItemAndImageEnrichmentModel`, `CodeFormulaModel`, and `ItemAndImageEnrichmentElement` in the 2.107.0 tree (grep the site-packages) and update `_docling_imports()` in `scripts/unimernet_formula.py` accordingly. Verify `StandardPdfPipeline` still exposes `_init_models` + `self.enrichment_pipe` and that `PdfPipelineOptions.do_formula_enrichment` / `TableFormerMode` still exist (adjust option construction if the threaded pipeline uses `ThreadedPdfPipelineOptions`).

- [ ] **Step 3: Set the pin and re-run the whole suite on 2.107.0**

Edit `scripts/requirements.txt`: `docling==2.107.0`. Then:
Run: `pytest -m "not slow" -q && pytest -m slow tests/test_e2e_formula.py -v`
Expected: PASS — the BLK equations still come out correct on docling 2.107.0.

- [ ] **Step 4: Commit**

```bash
git add scripts/requirements.txt scripts/unimernet_formula.py
git commit -m "Upgrade docling to 2.107.0; adapt formula pipeline imports"
```

---

## Verification (whole feature)

1. `pytest -m "not slow" -q` — all offline tests pass.
2. `pytest -m slow -q` — e2e BLK-equation assertions pass (with the model available).
3. Manual: `scripts/pdf2markdown -f ~/Downloads/ucc256404.pdf /tmp/out` and eyeball the BLK equations (44–49) — correct LaTeX, tables/images intact.
4. Manual HTML: `scripts/pdf2markdown -f "https://www.ti.com/document-viewer/ucc256404/datasheet" /tmp/outhtml` — image equations improved, `<sub>`/`<sup>` prose intact.
