"""Known image-handling defects observed converting real datasheets.

The ``known_defect`` tests reproduce image-pipeline defects seen on real
conversions (hardware-manual register diagrams, oscilloscope screenshots,
per-page header logo slivers, embedded GIF images) with synthetic images and
assert the CORRECT behavior. They fail until the converter is fixed; the
default suite auto-skips them. Heavy dependencies (RapidOCR, vtracer, the
formula model) are stubbed out.
"""

import base64
from pathlib import Path
from typing import Tuple
from unittest.mock import patch

import numpy as np
import pytest

import image_postprocess
import pdf2markdown
from image_postprocess import IMG_REF_RE, ImageType
from tests.fixtures.generate_duplicate_fixture import make_png
from tests.fixtures.known_defect_fixtures import (
    TINY_GIF,
    data_uri,
    make_register_bit_diagram_png,
    make_scope_screenshot_png,
    make_sliver_png,
    scope_ocr_regions,
)


class _StubOcr:
    pass


class _ScopeOcr:
    def regions(self, image_path):
        return scope_ocr_regions()


def _classify_all_diagram(image_path: Path, ocr) -> Tuple[ImageType, object]:
    return (ImageType.DIAGRAM, None)


def _stub_png_to_svg(png_path: Path, svg_path: Path) -> bool:
    svg_path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg"><title>{png_path.stem}</title></svg>',
        encoding="utf-8",
    )
    return True


def _image_targets(md_text: str) -> list:
    return [m.group(2).strip() for m in IMG_REF_RE.finditer(md_text)]


@pytest.mark.known_defect
def test_distinct_register_diagrams_not_collapsed(artifact_dir: Path):
    """Consecutive register bit-diagrams share their frame/grid template but
    differ in every label; near-duplicate detection must not collapse them
    into one canonical image (that silently replaces one register's diagram
    with another's)."""
    images_dir = artifact_dir / "doc.images"
    images_dir.mkdir()
    a = make_register_bit_diagram_png(
        images_dir, "reg_a", "CPG_PLL1_STBY (0x0C10)",
        ["RESETB", "SSC_EN", "SSC_MODE", "DIV_P"],
    )
    b = make_register_bit_diagram_png(
        images_dir, "reg_b", "CPG_PLL4_CLK1 (0x0C40)",
        ["FRACIN", "INTIN", "REFDIV", "POSTDIV"],
    )
    # Sanity: the two diagrams are genuinely different content.
    from PIL import Image
    pa, pb = np.array(Image.open(a)), np.array(Image.open(b))
    assert int((pa != pb).any(axis=2).sum()) > 500

    md_path = artifact_dir / "doc.md"
    md_path.write_text(
        "![PLL1 standby](doc.images/reg_a.png)\n\n"
        "![PLL4 clock 1](doc.images/reg_b.png)\n",
        encoding="utf-8",
    )

    with (
        patch.object(image_postprocess, "Ocr", _StubOcr),
        patch.object(image_postprocess, "classify", side_effect=_classify_all_diagram),
        patch.object(image_postprocess, "png_to_svg", side_effect=_stub_png_to_svg),
    ):
        counts = image_postprocess.postprocess(md_path, "doc.images")

    assert counts["visual_dedupe_refs"] == 0
    targets = set(_image_targets(md_path.read_text(encoding="utf-8")))
    assert len(targets) == 2, f"registers collapsed onto one image: {targets}"


@pytest.mark.known_defect
def test_scope_screenshot_text_not_inlined(artifact_dir: Path):
    """An oscilloscope screenshot whose OCR yields many confident UI captions
    must stay an image: inlining the captions replaces a measurement plot with
    meaningless prose (``Tek``, ``M Pos: 12.00ms``, ...)."""
    images_dir = artifact_dir / "doc.images"
    images_dir.mkdir()
    make_scope_screenshot_png(images_dir, "scope")
    md_path = artifact_dir / "doc.md"
    md_path.write_text(
        "The following capture shows the trigger output.\n\n"
        "![Trigger capture](doc.images/scope.png)\n",
        encoding="utf-8",
    )

    with (
        patch.object(image_postprocess, "Ocr", _ScopeOcr),
        patch.object(image_postprocess, "png_to_svg", side_effect=_stub_png_to_svg),
    ):
        counts = image_postprocess.postprocess(md_path, "doc.images")

    text = md_path.read_text(encoding="utf-8")
    assert counts["text"] == 0, "screenshot OCR captions were inlined as body text"
    assert "Tek" not in text
    assert any(t.startswith("doc.images/") for t in _image_targets(text)), (
        "the screenshot image reference was dropped"
    )


@pytest.mark.known_defect
def test_byte_distinct_header_slivers_removed(tmp_path: Path, monkeypatch):
    """Per-page header logo slivers (~157x25 px) are cropped once per page, so
    each file is byte-distinct and referenced only once — but they are still
    page furniture and none may survive into the output (every retained image
    should be taller than ~40 px)."""
    slivers_src = tmp_path / "src"
    slivers_src.mkdir()
    sliver_bytes = [
        make_sliver_png(slivers_src, f"sliver_{page}", page).read_bytes()
        for page in (1, 2, 3)
    ]
    figure_bytes = make_png(160, 80, [(10, 10, 10), (255, 255, 255)])

    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 fake")

    def fake_run_docling(source: Path, out_dir: Path, ocr: bool):
        embedded = out_dir / "doc.md"
        lines = ["Chapter body text.", ""]
        for page, data in enumerate(sliver_bytes, start=1):
            lines.append(f"![logo]({data_uri(data)})")
            lines.append(f"Page {page} prose.")
            lines.append("")
        lines.append(f"![Figure 1. Block diagram]({data_uri(figure_bytes)})")
        embedded.write_text("\n".join(lines), encoding="utf-8")
        return embedded, []

    monkeypatch.setattr(pdf2markdown, "run_docling", fake_run_docling)

    _, out_md, _, _ = pdf2markdown.convert(
        src, tmp_path / "out", ocr=False, force=False, postprocess=False,
    )

    from PIL import Image
    text = out_md.read_text(encoding="utf-8")
    retained = [t for t in _image_targets(text) if t.startswith("doc.images/")]
    assert retained, "the real figure must survive"
    for target in retained:
        with Image.open(out_md.parent / target) as img:
            width, height = img.size
        assert height > 40, (
            f"page-furniture sliver retained: {target} is {width}x{height}"
        )


@pytest.mark.known_defect
def test_gif_data_uri_externalized(tmp_path: Path, monkeypatch):
    """Embedded images must be externalized to the images directory whatever
    their format; a ``data:image/gif`` payload must not survive inline in the
    output Markdown."""
    png_bytes = make_png(60, 60, [(30, 60, 90), (200, 210, 220)])
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 fake")

    def fake_run_docling(source: Path, out_dir: Path, ocr: bool):
        embedded = out_dir / "doc.md"
        embedded.write_text(
            "\n".join([
                f"![diagram]({data_uri(png_bytes)})",
                "",
                f"![note icon]({data_uri(TINY_GIF, 'gif')})",
                "",
                "Body text.",
            ]),
            encoding="utf-8",
        )
        return embedded, []

    monkeypatch.setattr(pdf2markdown, "run_docling", fake_run_docling)

    _, out_md, _, _ = pdf2markdown.convert(
        src, tmp_path / "out", ocr=False, force=False, postprocess=False,
    )

    text = out_md.read_text(encoding="utf-8")
    assert "data:image" not in text, "an embedded image survived as inline base64"
    images_dir = out_md.parent / "doc.images"
    assert any(f.suffix == ".gif" for f in images_dir.iterdir()), (
        "the GIF payload was not written to the images directory"
    )


def test_alt_dump_variants_sanitized(tmp_path: Path):
    """Green guard: python-object dumps, embedded ``data:image`` payloads and
    over-long strings never survive as image alt text; pin that boundary."""
    data = make_png(12, 12, [(10, 20, 30)])
    encoded = base64.b64encode(data).decode("ascii")
    alts = [
        "<bound method FloatingItem.caption_text of ...>",
        f"caption data:image/png;base64,{encoded}",
        "x" * 400,
    ]
    embedded_md = tmp_path / "doc.embedded.md"
    embedded_md.write_text(
        "\n\n".join(f"![{alt}](data:image/png;base64,{encoded})" for alt in alts),
        encoding="utf-8",
    )
    out_md = tmp_path / "doc.md"
    entries = []

    pdf2markdown.split_images(
        embedded_md, out_md, tmp_path / "doc.images", image_entries=entries,
    )

    text = out_md.read_text(encoding="utf-8")
    assert "bound method" not in text
    assert "data:image" not in text
    assert [entry["alt"] for entry in entries] == ["Image", "Image", "Image"]
