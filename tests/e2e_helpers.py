"""Shared helpers for the slow end-to-end conversion tests.

`convert_pdf_for_tests` runs the real conversion pipeline on a synthetic PDF
with the formula recognizer stubbed so the large recognizer model is never
loaded. `convert_doc_for_tests` additionally runs a fixture builder first and
bundles everything a per-feature test needs into a `ConvertedDoc`.
"""

import json
import re
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import pytest

IMG_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
HEADING_RE = re.compile(r"(?m)^(#{1,6})[ \t]+(.*\S)[ \t]*$")
STUB_FORMULA_RE = re.compile(r"F_\{stub[A-Z]\}")


@dataclass
class ConvertedDoc:
    manifest: dict
    out_md: Path
    text: str          # final markdown as written by the pipeline
    plain: str         # text with markdown escapes undone (\_ -> _)
    images_dir: Path
    image_map: Optional[dict]
    stats: dict
    formula_calls: list  # image crops handed to the stubbed recognizer


def convert_pdf_for_tests(
    pdf: Path,
    out_dir: Path,
    recognize: Optional[Callable] = None,
) -> Tuple[Path, dict]:
    """Run the real conversion with the formula recognizer stubbed.

    The synthetic pages either contain no formulas or the test observes the
    stub's markers, so the large recognizer model must never load.
    """
    try:
        import docling  # noqa: F401
    except ModuleNotFoundError:
        pytest.skip("docling is not installed")
    import pdf2markdown
    import unimernet_formula

    if recognize is None:
        def recognize(img):
            return ""

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(unimernet_formula, "recognize", recognize)
        _, out_md, _, stats = pdf2markdown.convert(
            pdf, out_dir, ocr=False, force=False, postprocess=False,
        )
    return out_md, stats


def convert_doc_for_tests(builder: Callable, pdf: Path, out_dir: Path) -> ConvertedDoc:
    """Build a synthetic document PDF and convert it once.

    The recognizer stub returns counted, digit-free markers (``F_{stubA}``,
    ``F_{stubB}``, ...): digit-free so the equation-number reader can never
    turn a marker into a spurious ``\\tag{}``, counted so formula-region
    detection is observable in the output as ``$$F_{stubX}$$`` blocks.
    """
    try:
        manifest = builder(pdf)
    except ModuleNotFoundError as exc:
        pytest.skip(f"fixture dependency unavailable: {exc.name}")

    calls: list = []

    def fake_recognize(img):
        calls.append(img)
        return "F_{stub" + string.ascii_uppercase[(len(calls) - 1) % 26] + "}"

    out_md, stats = convert_pdf_for_tests(pdf, out_dir, recognize=fake_recognize)
    text = out_md.read_text(encoding="utf-8")
    images_dir = out_md.parent / f"{out_md.stem}.images"
    map_path = out_md.parent / f"{out_md.stem}.image-map.json"
    image_map = (
        json.loads(map_path.read_text(encoding="utf-8")) if map_path.is_file() else None
    )
    return ConvertedDoc(
        manifest=manifest,
        out_md=out_md,
        text=text,
        plain=text.replace("\\_", "_"),
        images_dir=images_dir,
        image_map=image_map,
        stats=stats,
        formula_calls=calls,
    )


# ---------------------------------------------------------------------------
# Assertion utilities
# ---------------------------------------------------------------------------

def table_rows(text: str) -> List[str]:
    return [line for line in text.splitlines() if line.lstrip().startswith("|")]


def headings(text: str) -> List[Tuple[int, str]]:
    return [(len(m.group(1)), m.group(2)) for m in HEADING_RE.finditer(text)]


def heading_titles(text: str) -> List[str]:
    return [title for _, title in headings(text)]


def find_rows(rows: List[str], *tokens: str) -> List[str]:
    """Rows in which all tokens co-occur (cells are space-joined by the
    serializer, so plain substring checks are safe)."""
    return [row for row in rows if all(token in row for token in tokens)]


def image_targets(text: str) -> List[str]:
    return [m.group(2).strip() for m in IMG_REF_RE.finditer(text)]


def pua_chars(text: str) -> List[str]:
    return [ch for ch in text if "\ue000" <= ch <= "\uf8ff"]


def stub_formula_blocks(text: str) -> List[str]:
    """Display-math blocks produced by the recognizer stub."""
    return [
        block for block in re.findall(r"\$\$(.+?)\$\$", text, flags=re.S)
        if STUB_FORMULA_RE.search(block)
    ]


def assert_common_invariants(doc: ConvertedDoc) -> None:
    """Cross-cutting output contract shared by every synthetic document."""
    assert "data:image" not in doc.text, "inline base64 image survived"

    assert doc.image_map is not None, "image map was not written"
    assert doc.image_map.get("version") == 1
    statuses = {entry.get("status") for entry in doc.image_map.get("entries", [])}
    assert statuses <= {"kept", "deduped", "inlined", "vectorized"}, (
        f"unexpected image statuses: {statuses}"
    )

    prefix = f"{doc.images_dir.name}/"
    referenced = {t[len(prefix):] for t in image_targets(doc.text) if t.startswith(prefix)}
    for name in referenced:
        assert (doc.images_dir / name).is_file(), f"dangling image reference: {name}"
    if doc.images_dir.is_dir():
        on_disk = {f.name for f in doc.images_dir.iterdir() if f.is_file()}
        assert on_disk == referenced, (
            f"orphan/missing image files: on_disk-referenced={on_disk - referenced}, "
            f"referenced-on_disk={referenced - on_disk}"
        )
