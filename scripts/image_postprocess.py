#!/usr/bin/env python3
"""
image_postprocess.py - Post-process images extracted from a converted PDF.

For every image referenced by a Markdown file, run OCR + structural analysis to
decide what it really is, and replace the raster image with something better:

  TABLE   -> reconstruct a Markdown table from OCR'd cells, inline it
  TEXT    -> inline the OCR'd text, dropping the image
  DIAGRAM -> trace the raster to SVG (vector graphics) and link the .svg instead
  PHOTO   -> leave the raster image untouched

This guards against docling leaving real text/tables as flat images, and turns
line-art diagrams into crisp vector graphics. Inlining is intentionally
*conservative*: an image is only flattened to text/table when detection is
high-confidence, so uncertain cases stay as images (SVG or PNG).

Detection / conversion logic is ported from
/home/andrei/2026/mdoc/vector_postprocessor.py (TableDetector, ImageClassifier,
PngToSvgConverter), with OCR done via RapidOCR (no system binary required).
"""

import logging
import re
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conservative tuning knobs
# ---------------------------------------------------------------------------
TABLE_MIN_CELLS = 6          # >= 2x3 grid cells suggests a table
TABLE_MIN_ROWS = 2
TABLE_MIN_COLS = 2

TEXT_MIN_REGIONS = 5         # need several OCR regions to trust "this is text"
TEXT_AREA_RATIO_MIN = 0.10   # OCR-covered area / image area
TEXT_MIN_MEAN_CONF = 0.65    # mean OCR confidence
TEXT_MAX_COLOR_UNIQUENESS = 0.05  # mostly few colors (ink on a plain ground)

DIAGRAM_SCORE_MIN = 0.5      # ImageClassifier diagram score above this -> vectorize
DIAGRAM_PHASH_THRESHOLD = 8  # max Hamming distance (out of 64 bits) to consider near-duplicate

# An image is treated as an EQUATION (LaTeX-OCR'd, not vectorised/OCR'd-as-text)
# when the Markdown labels it as one just before the ref, e.g. a standalone
# "Equation 12." paragraph (TI HTML datasheets). A cross-reference *link* like
# "[Equation 80](...)" is excluded via the (?<!\[) guard. The optional middle
# line absorbs the image-name paragraph docling emits ("GUID-...-low.gif").
EQUATION_LABEL_RE = re.compile(
    r'(?<!\[)\b(?:Equation|Eqn|Eq\.)\s*\(?\d+\)?[.:]?[ \t]*\r?\n+'
    r'(?:[ \t]*[^\n\[\]]{0,80}\r?\n+)?'   # optional short line (e.g. the GUID image-name)
    r'\Z', re.IGNORECASE)
EQUATION_LOOKBACK = 200      # chars of preceding Markdown to scan for the label

# Standalone "GUID-<hex>-low.gif" (or .png/.jpg) text paragraph docling emits as
# an image's name — noise once the image itself is inlined/linked; dropped.
GUID_TEXT_LINE_RE = re.compile(
    r'(?m)^[ \t]*GUID-[0-9A-Fa-f-]+(?:-low)?\.(?:gif|png|jpe?g|svg)[ \t]*$\n?')

# vtracer parameters (from mdoc PngToSvgConverter.convert defaults)
VTRACER_PARAMS = dict(
    colormode="color", hierarchical="stacked", mode="spline",
    filter_speckle=4, color_precision=6, layer_difference=16,
    corner_threshold=60, length_threshold=4.0, splice_threshold=45,
)

# Matches a Markdown image: ![alt](target)
IMG_REF_RE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')


class ImageType(Enum):
    TABLE = "table"
    TEXT = "text"
    EQUATION = "equation"
    DIAGRAM = "diagram"
    PHOTO = "photo"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# OCR (RapidOCR)
# ---------------------------------------------------------------------------
class Ocr:
    """Thin RapidOCR wrapper returning text regions with (x, y, w, h) bboxes.

    Uses PP-OCRv5 mobile models (det + rec, CH lang) which cover Simplified &
    Traditional Chinese, English and Japanese — both English and Japanese
    datasheets are read with a single engine, no per-doc switching.
    """

    def __init__(self) -> None:
        from rapidocr import RapidOCR, OCRVersion, LangDet, LangRec, ModelType
        # PP-OCRv5 only ships "mobile"/"server" variants (no "small", which is the
        # default that newer rapidocr would otherwise pick and reject). Pin mobile.
        self._engine = RapidOCR(params={
            "Det.ocr_version": OCRVersion.PPOCRV5, "Det.lang_type": LangDet.CH,
            "Det.model_type": ModelType.MOBILE,
            "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.lang_type": LangRec.CH,
            "Rec.model_type": ModelType.MOBILE,
        })

    def regions(self, image_path: Path) -> List[Dict]:
        try:
            res = self._engine(str(image_path))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("OCR failed for %s: %s", image_path.name, exc)
            return []

        boxes = getattr(res, "boxes", None)
        txts = getattr(res, "txts", None)
        scores = getattr(res, "scores", None)
        if boxes is None or txts is None:
            return []

        out: List[Dict] = []
        for box, txt, score in zip(boxes, txts, scores or [1.0] * len(txts)):
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            x, y = min(xs), min(ys)
            out.append({
                "text": (txt or "").strip(),
                "bbox": (x, y, max(xs) - x, max(ys) - y),
                "confidence": float(score),
            })
        return out


class LatexOcr:
    """Thin rapid_latex_ocr wrapper: render an equation image to a LaTeX string.

    ONNX-based (reuses onnxruntime, same family as RapidOCR); models download on
    first use. Lazily constructed so the model is only loaded when a datasheet
    actually contains labelled equations.
    """

    def __init__(self) -> None:
        from rapid_latex_ocr import LaTeXOCR
        self._engine = LaTeXOCR()

    def to_latex(self, image_path: Path) -> Optional[str]:
        try:
            with open(image_path, "rb") as fh:
                latex, _elapsed = self._engine(fh.read())
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("LaTeX-OCR failed for %s: %s", image_path.name, exc)
            return None
        latex = (latex or "").strip()
        return latex or None


def preceded_by_equation_label(text_before: str) -> bool:
    """True if `text_before` ends with a standalone "Equation N." label."""
    return EQUATION_LABEL_RE.search(text_before) is not None


def wrap_latex(latex: str) -> str:
    """Wrap a LaTeX string as Markdown math.

    Multi-line / environment LaTeX -> display block; otherwise inline `$…$`.
    """
    if "\\\\" in latex or "\\begin" in latex or "\n" in latex:
        return f"\n$$\n{latex}\n$$\n"
    return f"${latex}$"


# ---------------------------------------------------------------------------
# Table detection (ported from mdoc TableDetector)
# ---------------------------------------------------------------------------
def detect_table_structure(image_path: Path) -> Dict:
    """Detect a table via grid-line analysis. Returns is_table/confidence/cells/borders."""
    import cv2
    img = cv2.imread(str(image_path))
    if img is None:
        return {"is_table": False, "confidence": 0.0, "grid_cells": 0, "has_borders": False}

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
    detect_h = cv2.morphologyEx(gray, cv2.MORPH_OPEN, horizontal_kernel, iterations=2)
    horizontal = cv2.threshold(detect_h, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]

    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 40))
    detect_v = cv2.morphologyEx(gray, cv2.MORPH_OPEN, vertical_kernel, iterations=2)
    vertical = cv2.threshold(detect_v, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]

    table_mask = cv2.add(horizontal, vertical)
    contours, _ = cv2.findContours(table_mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    min_area = (img.shape[0] * img.shape[1]) * 0.001
    grid_cells = len([c for c in contours if cv2.contourArea(c) > min_area])
    is_table = grid_cells >= TABLE_MIN_CELLS
    has_borders = grid_cells > 0 and (
        cv2.countNonZero(horizontal) > gray.shape[0] * 0.3
        and cv2.countNonZero(vertical) > gray.shape[1] * 0.3
    )
    confidence = min(grid_cells / 20.0, 1.0) if is_table else 0.0
    return {"is_table": is_table, "confidence": confidence,
            "grid_cells": grid_cells, "has_borders": has_borders}


def _group_rows(regions: List[Dict], y_threshold: float = 20.0) -> List[List[Dict]]:
    """Group OCR regions into rows by y-proximity, each row sorted left-to-right."""
    if not regions:
        return []
    ordered = sorted(regions, key=lambda r: (r["bbox"][1], r["bbox"][0]))
    rows: List[List[Dict]] = []
    current: List[Dict] = []
    current_y = ordered[0]["bbox"][1]
    for region in ordered:
        if abs(region["bbox"][1] - current_y) <= y_threshold:
            current.append(region)
        else:
            if current:
                rows.append(current)
            current = [region]
            current_y = region["bbox"][1]
    if current:
        rows.append(current)
    for row in rows:
        row.sort(key=lambda r: r["bbox"][0])
    return rows


def table_rows_to_markdown(rows: List[List[Dict]]) -> Optional[str]:
    """Render grouped rows as a Markdown table (None if too small to be a table)."""
    if len(rows) < TABLE_MIN_ROWS:
        return None
    max_cols = max(len(r) for r in rows)
    if max_cols < TABLE_MIN_COLS:
        return None

    def cells(row: List[Dict]) -> List[str]:
        c = [r["text"].strip() for r in row]
        return c + [""] * (max_cols - len(c))

    lines = ["| " + " | ".join(cells(rows[0])) + " |",
             "|" + "|".join([" --- "] * max_cols) + "|"]
    lines += ["| " + " | ".join(cells(row)) + " |" for row in rows[1:]]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Image metrics (ported from mdoc ImageClassifier.analyze_image)
# ---------------------------------------------------------------------------
def image_metrics(image_path: Path) -> Dict:
    """Compute edge density, color variance/uniqueness and a diagram score."""
    from PIL import Image
    from scipy import ndimage
    try:
        arr = np.array(Image.open(image_path).convert("RGB"))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not read %s: %s", image_path.name, exc)
        return {"edge_density": 0.0, "color_uniqueness": 1.0,
                "is_diagram_score": 0.0, "shape": (0, 0)}

    edges = ndimage.sobel(arr.mean(axis=2))
    edge_density = float(np.mean(np.abs(edges)) / 255.0)
    color_std = float(np.std(arr) / 255.0)
    unique_colors = len(np.unique(arr.reshape(-1, 3), axis=0))
    total_pixels = arr.shape[0] * arr.shape[1]
    color_uniqueness = unique_colors / total_pixels if total_pixels else 1.0

    score = edge_density * 0.4 + (1 - color_std) * 0.3 + (1 - min(color_uniqueness * 10, 1.0)) * 0.3
    if edge_density > 0.15:
        score += 0.15
    return {"edge_density": edge_density, "color_uniqueness": color_uniqueness,
            "is_diagram_score": score, "shape": arr.shape[:2]}


def is_text_image(regions: List[Dict], metrics: Dict) -> bool:
    """Conservative test: is this image essentially a block of text?"""
    if len(regions) < TEXT_MIN_REGIONS:
        return False
    h, w = metrics.get("shape", (0, 0))
    area = h * w
    if not area:
        return False
    text_area = sum(r["bbox"][2] * r["bbox"][3] for r in regions)
    mean_conf = sum(r["confidence"] for r in regions) / len(regions)
    return (text_area / area >= TEXT_AREA_RATIO_MIN
            and mean_conf >= TEXT_MIN_MEAN_CONF
            and metrics.get("color_uniqueness", 1.0) <= TEXT_MAX_COLOR_UNIQUENESS)


def text_rows_to_markdown(rows: List[List[Dict]]) -> str:
    """Render grouped OCR rows as plain Markdown text (one line per detected row)."""
    return "\n".join(" ".join(r["text"] for r in row).strip() for row in rows).strip()


# ---------------------------------------------------------------------------
# PNG -> SVG (ported from mdoc PngToSvgConverter)
# ---------------------------------------------------------------------------
def png_to_svg(png_path: Path, svg_path: Path) -> bool:
    try:
        import vtracer
        vtracer.convert_image_to_svg_py(str(png_path), str(svg_path), **VTRACER_PARAMS)
        return True
    except Exception as exc:
        logger.warning("PNG->SVG failed for %s: %s", png_path.name, exc)
        return False


# ---------------------------------------------------------------------------
# Perceptual hash for near-duplicate diagram detection
# ---------------------------------------------------------------------------
def _hamming(a: bytes, b: bytes) -> int:
    diff = np.frombuffer(a, dtype=np.uint8) ^ np.frombuffer(b, dtype=np.uint8)
    return int(np.unpackbits(diff).sum())


def diagram_phash(image_path: Path) -> bytes:
    """Return a 64-bit dHash fingerprint for near-duplicate diagram detection.

    Composites transparency on white, trims the background border, resizes to
    9x8, converts to greyscale, then computes column-wise difference hash.
    Returns b'\\x00'*8 on any error so callers can treat it as "no match".
    """
    try:
        from PIL import Image, ImageChops
        img = Image.open(image_path).convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        rgb = bg.convert("RGB")

        # Trim border pixels that are close to the corner background colour.
        corner = rgb.getpixel((0, 0))
        diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, corner))
        threshold = diff.point(lambda x: 0 if x <= 8 else 255)
        bbox = threshold.getbbox()
        if bbox:
            pad = 1
            w, h = rgb.size
            rgb = rgb.crop((
                max(0, bbox[0] - pad), max(0, bbox[1] - pad),
                min(w, bbox[2] + pad), min(h, bbox[3] + pad),
            ))

        small = rgb.resize((9, 8), Image.LANCZOS).convert("L")
        arr = np.array(small, dtype=np.uint8)       # shape (8, 9)
        diff_arr = arr[:, 1:] > arr[:, :-1]         # shape (8, 8), bool
        return np.packbits(diff_arr.flatten()).tobytes()
    except Exception as exc:
        logger.debug("diagram_phash failed for %s: %s", image_path.name, exc)
        return b"\x00" * 8


# ---------------------------------------------------------------------------
# Classification + orchestration
# ---------------------------------------------------------------------------
def classify(image_path: Path, ocr: Ocr) -> Tuple[ImageType, object]:
    """Return (ImageType, payload). Payload is markdown str for TABLE/TEXT, else None."""
    regions = ocr.regions(image_path)            # OCR every image (per the requirement)
    table_info = detect_table_structure(image_path)
    metrics = image_metrics(image_path)

    if table_info["is_table"] and table_info["has_borders"]:
        md_table = table_rows_to_markdown(_group_rows(regions))
        if md_table:
            return ImageType.TABLE, md_table

    if is_text_image(regions, metrics):
        text = text_rows_to_markdown(_group_rows(regions))
        if text:
            return ImageType.TEXT, text

    if metrics["is_diagram_score"] > DIAGRAM_SCORE_MIN:
        return ImageType.DIAGRAM, None

    return ImageType.PHOTO, None


def postprocess(md_path: Path, images_dirname: str) -> Dict[str, int]:
    """Rewrite md_path in place, replacing image refs per their classification.

    Returns a count of each ImageType handled. Orphaned image files (replaced by
    inline text/table or by an SVG) are removed from the images directory.
    """
    content = md_path.read_text(encoding="utf-8")
    images_dir = md_path.parent / images_dirname
    prefix = f"{images_dirname}/"

    ocr: Optional[Ocr] = None
    latex_ocr: Optional[LatexOcr] = None
    decisions: Dict[str, Tuple[ImageType, object]] = {}  # ref-target -> (type, payload)
    equations: Dict[str, Optional[str]] = {}  # ref-target -> LaTeX (None if OCR failed)
    counts = {t.value: 0 for t in ImageType}
    counts["visual_dedupe_refs"] = 0
    counts["visual_dedupe_files"] = 0
    canonical_diagrams: Dict[bytes, Path] = {}  # fingerprint -> canonical SVG path

    def decide(target: str) -> Tuple[ImageType, object]:
        nonlocal ocr
        if target in decisions:
            return decisions[target]
        img_path = md_path.parent / target
        if not img_path.is_file():
            decisions[target] = (ImageType.UNKNOWN, None)
            return decisions[target]
        if ocr is None:
            ocr = Ocr()
        result = classify(img_path, ocr)
        counts[result[0].value] += 1
        decisions[target] = result
        return result

    def equation_latex(target: str) -> Optional[str]:
        """LaTeX for an equation-labelled image (cached); None if unreadable."""
        nonlocal latex_ocr
        if target in equations:
            return equations[target]
        img_path = md_path.parent / target
        if not img_path.is_file():
            equations[target] = None
            return None
        if latex_ocr is None:
            latex_ocr = LatexOcr()
        latex = latex_ocr.to_latex(img_path)
        equations[target] = latex
        return latex

    def replace(match: "re.Match[str]") -> str:
        alt, target = match.group(1), match.group(2).strip()
        # Only touch images that live in our extracted-images directory.
        if not target.startswith(prefix):
            return match.group(0)

        # Equation? A standalone "Equation N." label just before the ref
        # (TI HTML datasheets) is a high-precision signal — LaTeX-OCR it.
        before = match.string[max(0, match.start() - EQUATION_LOOKBACK):match.start()]
        if preceded_by_equation_label(before):
            latex = equation_latex(target)
            if latex:
                counts[ImageType.EQUATION.value] += 1
                return wrap_latex(latex)
            # LaTeX-OCR failed → fall through, handle as an ordinary image.

        kind, payload = decide(target)
        if kind in (ImageType.TABLE, ImageType.TEXT):
            return f"\n{payload}\n"
        if kind == ImageType.DIAGRAM:
            png_path = md_path.parent / target
            svg_path = png_path.with_suffix(".svg")

            # If this exact target was already processed (PNG traced → SVG on a
            # prior ref), the SVG exists and the PNG is gone — just reuse it.
            if svg_path.is_file() and not png_path.is_file():
                return f"![{alt}]({prefix}{svg_path.name})"

            if not png_path.is_file():
                return match.group(0)

            fingerprint = diagram_phash(png_path)
            canonical_svg: Optional[Path] = None
            for known_fp, known_svg in canonical_diagrams.items():
                if _hamming(fingerprint, known_fp) <= DIAGRAM_PHASH_THRESHOLD:
                    canonical_svg = known_svg
                    break

            if canonical_svg is not None:
                png_path.unlink()
                counts["visual_dedupe_files"] += 1
                counts["visual_dedupe_refs"] += 1
                return f"![{alt}]({prefix}{canonical_svg.name})"

            if png_to_svg(png_path, svg_path):
                canonical_diagrams[fingerprint] = svg_path
                return f"![{alt}]({prefix}{svg_path.name})"
            return match.group(0)  # keep PNG if tracing failed
        return match.group(0)  # PHOTO / UNKNOWN: leave as-is

    new_content = IMG_REF_RE.sub(replace, content)
    # Drop the standalone "GUID-...-low.gif" image-name paragraphs docling emits.
    new_content = GUID_TEXT_LINE_RE.sub("", new_content)
    md_path.write_text(new_content, encoding="utf-8")

    # Remove image files no longer referenced by the rewritten Markdown.
    removed = 0
    if images_dir.is_dir():
        referenced = {
            (md_path.parent / m.group(2).strip()).resolve()
            for m in IMG_REF_RE.finditer(new_content)
            if m.group(2).strip().startswith(prefix)
        }
        for f in images_dir.iterdir():
            if f.is_file() and f.resolve() not in referenced:
                f.unlink()
                removed += 1
        if not any(images_dir.iterdir()):
            images_dir.rmdir()

    counts["removed_files"] = removed
    return counts


__all__ = ["postprocess", "ImageType", "diagram_phash", "_hamming", "DIAGRAM_PHASH_THRESHOLD",
           "LatexOcr", "preceded_by_equation_label", "wrap_latex", "EQUATION_LABEL_RE"]
