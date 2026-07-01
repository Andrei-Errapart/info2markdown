# Automatic Conversion of Images to LaTeX Formulas: A Technical Investigation

*State of the field as of mid-2026*

---

## 1. The core problem and why it is hard

Formula recognition (often called Mathematical Expression Recognition, MER, or when handwritten, HMER) is the task of converting an image of a mathematical expression into a symbolic markup — almost always LaTeX. It sits at the intersection of computer vision and sequence generation, and it is harder than ordinary OCR for three structural reasons:

- **Two-dimensional syntax.** Unlike prose, math has meaningful vertical and nested structure — superscripts, subscripts, fractions, matrices, stacked limits, radicals. A recognizer must infer a tree, not a line.
- **Representational non-uniqueness.** The *same* rendered formula can be produced by many different LaTeX strings (`\frac{a}{b}` vs `a \over b`; `x^{2}` vs `x^2`; spacing, `\left(` vs `(`). This one fact poisons naive evaluation (see §6) and complicates training.
- **Long-range dependencies and brittle delimiters.** A single missing `}` breaks the whole expression. Sequence models must balance delimiters across long token spans.

The task splits into two fairly distinct regimes:

| Regime | Input | Canonical datasets | Difficulty driver |
|---|---|---|---|
| **Printed / typeset (PMER)** | Rendered LaTeX, screenshots, PDF crops | im2latex-100k, UniMER-Test (SPE/CPE/SCE), realFormula | Notation complexity, font/scale variation, screen capture noise |
| **Handwritten (HMER)** | Scanned/online strokes | CROHME 2014/16/19/23, HME100K, MathWriting | Writing-style variance, segmentation, ambiguity |

---

## 2. Architectural evolution

The field has moved through four generations, and the frontier is currently fragmenting between specialist and generalist approaches.

### 2.1 CNN encoder + RNN decoder with attention (2016–2020)
The founding architecture, established by Deng et al.'s **im2markup / WYGIWYS** ("What You Get Is What You See", the paper that also gave us im2latex-100k). A CNN extracts visual features; an attention-equipped LSTM decodes the LaTeX token sequence with coarse-to-fine attention over the image. This defined the task but struggled with long sequences — accuracy degrades for tokens far from the start of the expression.

### 2.2 Tree-structured and syntax-aware decoders (2020–2023)
Because math *is* a tree, several works replaced the flat string decoder with structure-aware decoding: tree decoders (DenseWAP-TD), syntax-aware networks (SAN), and bidirectionally-trained transformers (**BTTR**, then the improved **CoMER** with coverage attention). These improved handwritten ExpRate meaningfully and remain the backbone of the academic HMER line. More recent entries in this lineage:
- **TAMER** (Tree-Aware transformER) — hybrids tree structure with the transformer decoder.
- **NAMER** — non-autoregressive decoding for HMER, targeting speed.
- **Uni-MuMER** (2025) — unified *multi-task fine-tuning* of a VLM specifically for handwritten recognition.

### 2.3 Pure transformer encoder–decoder / ViT (2021–2024)
The now-standard specialist design: a **Vision Transformer or convolutional-ViT hybrid encoder** feeds a **transformer decoder** that autoregressively emits LaTeX. Transformer architectures consistently beat the CNN-RNN baseline on accuracy, BLEU, and edit distance, thanks to self-attention and positional embeddings in both encoder and decoder. Key instances:

- **pix2tex / LaTeX-OCR** (Lukas Blecher) — the popular open-source ViT→transformer implementation; ~25M params. Great baseline, but hallucinates on text and is weak on complex/real-world input.
- **Nougat** (Meta) — encoder-decoder pretrained for *full-page* academic-PDF → markup. Excellent for whole papers; over-hallucinates on tiny math-only crops.
- **Texify** (VikParuchuri) — im2latex-trained; **now deprecated**, functionality migrated into **Surya** (`surya_latex_ocr`).
- **MathNet** — convolutional-ViT, introduced alongside the improved im2latexv2 benchmark and realFormula.
- **Sumen** — end-to-end transformer trained on large datasets, strong BLEU.
- **UniMERNet** (OpenDataLab, 2024) — the formula model you've validated as your best (it beat docling's CodeFormulaV2 on born-digital PDF equations). A robust universal recognizer trained on the million-scale UniMER-1M set, explicitly engineered for *real-world* messiness (screen captures, handwriting, complex printed). At release it matched commercial Mathpix and beat all open-source peers. ~392M params in the full version; a smaller/faster variant shipped Sept 2024.

### 2.4 Vision-Language Models (2024–present) — the current inflection
Two flavors, and this is where the state of the art now lives:

**(a) General-purpose VLMs** — GPT-4o/GPT-5.x, Gemini 2.5/3 Pro, Claude, Qwen2.5-VL / Qwen3-VL, InternVL3. These do formula recognition zero-shot as a byproduct of general multimodal training. They are **remarkably robust to visual noise and layout variation** but historically less precise on strict fine-grained LaTeX structure than dedicated models.

**(b) Specialized document-parsing VLMs** — compact (0.9B–3B) models purpose-built for document→markup, which now **dominate the benchmarks** while being small enough to self-host:
- **MinerU 2.5 / 2.5-Pro** (1.2B) — decoupled two-stage (layout, then content) VLM.
- **PaddleOCR-VL / -VL-1.5** (0.9B) — ultra-compact multilingual parser.
- **GLM-OCR** (0.9B), **dots.ocr**, **MonkeyOCR**, **DeepSeek-OCR**, **olmOCR 2**, **Dolphin** — a very crowded and fast-moving field.

The striking finding of 2025–26 is that **0.9–1.2B specialist VLMs now beat 100B+ generalists** on document parsing including formula recognition.

---

## 3. The current leaderboard (formula recognition, CDM metric)

The authoritative public benchmark is **OmniDocBench** (Shanghai AI Lab, CVPR 2025), which evaluates document parsing across text, tables, formulas, and reading order. Its **Formula-block** subtask crops ~1050 formula sub-images from real pages (using ground-truth boxes to isolate recognition from layout errors) and scores with **CDM** (see §6).

**OmniDocBench v1.5, Formula-block CDM** — the scores below are re-evaluated under a unified environment in the GLM-OCR technical report (arXiv 2603.10910; early-2026 snapshot), on a **0–100 scale** (higher is better). Absolute values drift across sources and model versions, so read them as *directional*, not exact (see §6):

| Model | Params | Formula CDM (v1.5) |
|---|---|---|
| **PaddleOCR-VL-1.5** | 0.9B | **94.21** |
| **GLM-OCR** | 0.9B | 93.90 |
| Gemini-3 Pro | API / huge | 89.18 |
| **MinerU 2.5** | 1.2B | 88.46 |
| GPT-5.2 | API / huge | 86.11 |
| DeepSeek-OCR2 | — | 83.37 |

Two 0.9B specialists sit clearly on top, and the frontier general VLMs (Gemini-3 Pro, GPT-5.2) land *below* the compact specialists on strict formula CDM — the headline finding of the 2025–26 cycle.

*A caution on cross-source numbers:* other snapshots disagree on the absolutes. The PaddleOCR-VL paper reports its own model at 90.88, MinerU 2.5 at 87.55, and **dots.ocr at 85.34**, whereas isolated-formula-block evaluations elsewhere score dots.ocr far lower — it emits little usable LaTeX when handed a bare formula crop, despite being strong at full-page parsing, so its sub-task and end-to-end scores diverge sharply. The live OmniDocBench board has since moved on again (Gemini-3-Flash now tops its overall table). Treat any single formula-CDM figure as a snapshot, not a constant.

**On the earlier v1.0 full-document evaluation**, the three headline formula recognizers — **GPT-4o (86.8), Mathpix (86.6), UniMERNet (85.0)** — were essentially tied around ~86% CDM, with GPT-4o notable for the highest *strict* recall (65.5%, i.e. perfect character accuracy). That near-tie is the "old" state of the art that UniMERNet belongs to; the compact specialist VLMs above have since pulled clearly ahead.

**Component leaderboards on other suites** (from vendor technical reports, so read with mild caution): on the UniMER-Test suite — per the same GLM-OCR report (arXiv 2603.10910) — GLM-OCR (96.5), MinerU 2.5 (96.4), PaddleOCR-VL-1.5 (96.1), and Gemini-3 Pro (96.4) cluster at the top, with GPT-5.2 around 90.5 and DeepSeek-OCR2 around 85.8.

### Takeaway ranking (printed / real-world formulas)
1. **PaddleOCR-VL / MinerU 2.5** — current open-source SOTA, self-hostable, small.
2. **Mathpix** — still the commercial reference; best hands-off robustness, especially handwriting/whiteboards.
3. **Frontier general VLMs** (Gemini 3 Pro, GPT-5.x, Qwen3-VL) — excellent, most robust to weird inputs, but API-bound or huge.
4. **PP-FormulaNet-L** — best *pure formula specialist* if you want a single-purpose model (≈6% over UniMERNet on BLEU; -S variant 16× faster).
5. **UniMERNet** — still solid and a great annotation/reference model, but no longer the frontier.

---

## 4. Handwritten recognition (a separate contest)

If your input is handwriting, the leaderboard is different and the metric is usually **ExpRate** (exact expression match) on **CROHME**, increasingly reported as **ExpRate@CDM**.

- The **CROHME** series (2014: 986 test / 2016: 1147 / 2019: 1199; 2023 adds ~2300) remains the standard benchmark; data is InkML stroke trajectories rendered to bitmaps. Training set is small (~8.8k), which caps achievable accuracy.
- **HME100K** (74.5k train / 24.6k test) is the large real-world camera-captured set.
- **MathWriting** (Google, 2024) is now the **largest** HME corpus — 230k human + 400k synthetic expressions — and is shifting the field.
- **Specialist SOTA**: CoMER-lineage models, TAMER, and Uni-MuMER lead the academic ExpRate tables. UniMERNet, despite not being handwriting-specialized, surpasses prior handwritten SOTA on all CROHME test sets (~65–68% exact).
- **Generalists**: GPT-4o (~48.8% avg ExpRate) and Doubao-1.5-pro (~48.8%) trail specialists on clean CROHME but generalize better to messy real handwriting.

For handwriting specifically, **Mathpix remains the most reliable off-the-shelf option**, with the specialist academic models worth it only if you can fine-tune on your domain.

---

## 5. Full-document pipelines (when formulas are embedded in pages)

If your real goal is "PDF/scan → markdown with inline LaTeX" rather than "one cropped formula → LaTeX", you want a document-parsing system, not a bare formula model. These orchestrate layout detection → text OCR → formula recognition → table recognition:

- **MinerU** (OpenDataLab) — the strongest open-source all-rounder. Converts PDF/image/DOCX/PPTX/XLSX → Markdown/JSON, auto-converts formulas to LaTeX and tables to HTML, 109 languages, CLI + FastAPI + Gradio, runs CPU/CUDA/MPS. Recently relicensed from AGPLv3 to a custom Apache-2.0-based license (the "MinerU Open Source License" — Apache-2.0 *with additional conditions*, so much friendlier than AGPL for commercial/self-host use, but check the extra clauses before shipping). v2.5 uses a 1.2B decoupled VLM; older pipeline builds used UniMERNet for the math component. **This is likely your best self-hosted default.**
- **Marker** (VikParuchuri) — fast modular pipeline, historically used pix2tex for formulas; good table rendering, quicker than MinerU but slightly lower fidelity.
- **PaddleOCR / PP-StructureV3 / PaddleOCR-VL** — Baidu's ecosystem; PP-FormulaNet is the formula component you can also use standalone.
- **Docling** (IBM), **olmOCR 2** (AllenAI, RLVR-trained 7B), **dots.ocr**, **GOT-OCR 2.0**, **Nougat** — other viable open options with different speed/accuracy/layout tradeoffs.
- **Commercial**: Mathpix (Snip app + PDF + API), Mistral OCR, plus the frontier VLM APIs.

---

## 6. Evaluation: why the metric matters more than you'd think

This deserves emphasis because it changes how you should read every "X% better" claim, including the ones in this document.

**The problem with BLEU / Edit Distance / ExpRate:** they compare LaTeX *strings*. Because the same formula has many valid string encodings, these metrics penalize correct predictions that happen to use different (equally valid) markup, and — worse — can *reward* visually-wrong predictions that share token n-grams with the ground truth. Concretely, a wrong numeral can score high on BLEU because the surrounding style matches, while a perfect answer in a different style scores low.

**CDM (Character Detection Matching)** — the fix, introduced by the UniMERNet team and accepted at **CVPR 2025**. Instead of comparing strings, CDM **renders both the predicted and ground-truth LaTeX to images**, then does spatially-aware character-level visual matching between them. This makes the score invariant to LaTeX representation differences and aligns much better with human judgment. It is now the field standard, integrated directly into OmniDocBench.

Practical implications for you:
- **Benchmark with CDM, not BLEU**, if you evaluate candidates on your own data. The reference implementation lives in the UniMERNet repo (`/cdm`); it needs TeX Live + ImageMagick + Ghostscript (v1.6 rewrote the Node/KaTeX dependency in Python for ~3× speedup).
- Treat any BLEU-based "6% better than UniMERNet" claim as directional, not precise.
- **ExpRate@CDM** is the emerging standard for handwriting.
- Beware vendor self-reported component scores measured under non-identical conditions; OmniDocBench's re-evaluation under a unified environment is the trustworthy source.

---

## 7. Concrete recommendations

Since you're self-hosting-inclined and running a **docling-based pipeline** (today on docling's CodeFormulaV2 for equations, with UniMERNet-base already validated as a better formula model):

**If your pipeline is already docling-based (as info2markdown is) — read this first:**
The high-value change is *not* swapping document engines — it's swapping the formula model *inside* docling. The pipeline currently uses docling's **CodeFormulaV2** (PDF equations via `--enrich-formula`; HTML image-equations via CodeFormulaV2 loaded directly). Since you've already confirmed **UniMERNet-base ≫ CodeFormulaV2** on born-digital PDF equations, the lowest-risk, highest-payoff move is to route detected equation regions to UniMERNet-base rather than adopting a whole new engine. **PP-FormulaNet-L** is the other specialist worth A/B-ing in that same slot (≈6% BLEU over UniMERNet on its own suite — validate on your data with CDM). Treat the MinerU / PaddleOCR-VL picks below as *greenfield* options: dropping them into a docling architecture is an engine replacement, not a model swap. And mind the input caveat — every benchmark here is on real-page / screen-capture crops, whereas your born-digital PDFs keep a text/vector layer that `--enrich-formula` can exploit, so validate on born-digital samples specifically rather than trusting the crop-based ranking.

**If you want a drop-in upgrade for isolated formula images:**
- **PP-FormulaNet-L** — best pure-formula specialist, self-hostable, no API. Drop to **-S** for 16× throughput on batch jobs.
- **PaddleOCR-VL (0.9B)** — if you're willing to run a small VLM, it's the current CDM leader and also handles text/tables.

**If your real input is pages/PDFs/scans with embedded math:**
- **MinerU 2.5** — strongest open-source end-to-end, now Apache-based license, runs on your hardware (including MPS on the M5 Pro), outputs Markdown+LaTeX. This is the pragmatic pick for a homelab document pipeline (pairs naturally with your Paperless-ngx / NAS setup).

**If you want maximum accuracy on messy/handwritten input and don't mind a service:**
- **Mathpix** — still the robustness reference, especially for handwriting and whiteboard photos.
- **Gemini 3 Pro / GPT-5.x / Qwen3-VL** via API — most robust to unusual inputs; good when you can't predict input quality.

**Whatever you pick, validate on your own corpus with CDM**, not BLEU — the ranking can flip depending on whether your formulas are English/Chinese, printed/handwritten, clean/degraded, and short/long.

---

## 8. Key references

- Deng et al., *Image-to-Markup Generation with Coarse-to-Fine Attention* (im2latex-100k), ICML 2017
- Wang et al., *UniMERNet: A Universal Network for Real-World Mathematical Expression Recognition*, arXiv 2404.15254
- Wang et al., *Image Over Text: Transforming Formula Recognition Evaluation with Character Detection Matching* (CDM), CVPR 2025 — arXiv 2409.03643
- Ouyang et al., *OmniDocBench: Benchmarking Diverse PDF Document Parsing*, CVPR 2025 — arXiv 2412.07626; live leaderboard at github.com/opendatalab/OmniDocBench
- Liu et al., *PP-FormulaNet: Bridging Accuracy and Efficiency in Advanced Formula Recognition*, arXiv 2503.18382
- *MinerU2.5: A Decoupled Vision-Language Model for Efficient High-Resolution Document Parsing*, arXiv 2509.22186
- *PaddleOCR-VL: Boosting Multilingual Document Parsing via a 0.9B Ultra-Compact VLM*, arXiv 2510.14528
- GLM-OCR technical report, arXiv 2603.10910 — source of the unified-environment OmniDocBench v1.5 Formula-block CDM and UniMER-Test figures cited in §3
- Gervais et al., *MathWriting: A Dataset for Handwritten Mathematical Expression Recognition*, 2024
- CROHME 2023 competition report (ICDAR)
