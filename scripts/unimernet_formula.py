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
    r"""Drop a trailing ``\eqno …`` run — UniMERNet reads the page's right-margin
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
    r"""Recognise a single equation image, returning LaTeX (no ``\eqno``)."""
    import torch
    model, proc = _load()
    pixel = proc(img.convert("RGB")).unsqueeze(0).to("cpu")
    with torch.no_grad():
        out = model.generate({"image": pixel})
    pred = out["pred_str"]
    latex = pred[0] if isinstance(pred, (list, tuple)) else str(pred)
    return strip_eqno(latex)


def _docling_imports():
    """All docling symbols the pipeline swap needs, in one place (eases upgrades)."""
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


(DocumentConverter, PdfFormatOption, InputFormat, PdfPipelineOptions,
 TableFormerMode, StandardPdfPipeline, _BaseEnrich, _CodeFormulaModel,
 _DocItemLabel, _TextItem) = _docling_imports()


class UniMERNetFormulaModel(_BaseEnrich):
    r"""docling enrichment model: OCR each FORMULA crop with UniMERNet-base.

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
