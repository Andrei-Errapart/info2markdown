"""
tests/test_visual_dedupe.py

Unit tests for perceptual-hash diagram deduplication in image_postprocess.postprocess().

No vtracer, no RapidOCR, no cv2 required — all heavy imports are monkeypatched.
"""
import shutil
from pathlib import Path
from typing import Tuple
from unittest.mock import patch

import pytest

import image_postprocess
from image_postprocess import (
    ImageType,
    _hamming,
    diagram_phash,
    DIAGRAM_PHASH_THRESHOLD,
    postprocess,
)


# ---------------------------------------------------------------------------
# Helpers — image factories
# ---------------------------------------------------------------------------

def _make_logo_png(
    directory: Path,
    name: str,
    padding: int,
    fill: Tuple[int, int, int] = (30, 80, 160),
) -> Path:
    """White-bordered filled rectangle. Varying padding simulates the same logo
    with different amounts of surrounding whitespace."""
    from PIL import Image, ImageDraw

    inner_w, inner_h = 40, 20
    outer_w = inner_w + 2 * padding
    outer_h = inner_h + 2 * padding
    img = Image.new("RGB", (outer_w, outer_h), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle(
        [padding, padding, padding + inner_w - 1, padding + inner_h - 1],
        fill=fill,
    )
    path = directory / f"{name}.png"
    img.save(str(path), format="PNG")
    return path


def _make_distinct_diagram_png(directory: Path, name: str) -> Path:
    """Diagonal orange stripes on grey — structurally different from a logo."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (60, 40), (220, 220, 220))
    draw = ImageDraw.Draw(img)
    for i in range(0, 70, 8):
        draw.line([(i, 0), (i + 40, 40)], fill=(180, 60, 10), width=3)
    path = directory / f"{name}.png"
    img.save(str(path), format="PNG")
    return path


def _build_md(images_dirname: str, names: list) -> str:
    lines = ["# Test Document", ""]
    for name in names:
        lines.append(f"![{name}]({images_dirname}/{name}.png)")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Monkeypatching helpers
# ---------------------------------------------------------------------------

def _classify_all_diagram(image_path: Path, ocr) -> Tuple[ImageType, object]:
    return (ImageType.DIAGRAM, None)


def _stub_png_to_svg(png_path: Path, svg_path: Path) -> bool:
    svg_path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg"><title>{png_path.stem}</title></svg>',
        encoding="utf-8",
    )
    return True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def logo_workspace(artifact_dir: Path):
    """
    Three near-identical logo PNGs (padding 2, 6, 10) and one distinct diagram.
    Returns (md_path, images_dir).
    """
    images_dir = artifact_dir / "doc.images"
    images_dir.mkdir()

    _make_logo_png(images_dir, "logo_a", padding=2)
    _make_logo_png(images_dir, "logo_b", padding=6)
    _make_logo_png(images_dir, "logo_c", padding=10)
    _make_distinct_diagram_png(images_dir, "distinct")

    md_path = artifact_dir / "doc.md"
    md_path.write_text(
        _build_md("doc.images", ["logo_a", "logo_b", "logo_c", "distinct"]),
        encoding="utf-8",
    )
    return md_path, images_dir


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

def test_near_duplicate_logos_deduplicate_to_single_svg(logo_workspace):
    md_path, images_dir = logo_workspace

    with (
        patch.object(image_postprocess, "classify", side_effect=_classify_all_diagram),
        patch.object(image_postprocess, "png_to_svg", side_effect=_stub_png_to_svg),
    ):
        counts = postprocess(md_path, "doc.images")

    # All 4 images classified as DIAGRAM.
    assert counts["diagram"] == 4

    # 2 of the 3 logo refs reused the canonical SVG.
    assert counts["visual_dedupe_refs"] == 2
    assert counts["visual_dedupe_files"] == 2

    svg_files = sorted(f.name for f in images_dir.iterdir() if f.suffix == ".svg")
    png_files = sorted(f.name for f in images_dir.iterdir() if f.suffix == ".png")

    assert len(svg_files) == 2, f"expected 2 SVGs, got: {svg_files}"
    assert png_files == [], f"unexpected PNGs remaining: {png_files}"

    md_text = md_path.read_text(encoding="utf-8")

    # All 4 refs now point at SVG files.
    svg_lines = [line for line in md_text.splitlines() if ".svg)" in line]
    assert len(svg_lines) == 4

    # The 3 logo refs all point to the same canonical SVG.
    logo_svg_targets = set()
    for line in md_text.splitlines():
        if any(name in line for name in ("logo_a", "logo_b", "logo_c")) and ".svg)" in line:
            logo_svg_targets.add(line.split("(")[1].rstrip(")"))
    assert len(logo_svg_targets) == 1, (
        f"expected all logo refs to point to 1 canonical SVG, got: {logo_svg_targets}"
    )

    # The distinct diagram points to a different SVG.
    distinct_target = None
    for line in md_text.splitlines():
        if "distinct" in line and ".svg)" in line:
            distinct_target = line.split("(")[1].rstrip(")")
    assert distinct_target is not None
    assert distinct_target not in logo_svg_targets, (
        f"distinct diagram was incorrectly merged with logo canonical: {distinct_target}"
    )


def test_distinct_diagram_is_not_deduplicated(artifact_dir):
    images_dir = artifact_dir / "doc.images"
    images_dir.mkdir()
    _make_logo_png(images_dir, "diag_x", padding=4)
    _make_distinct_diagram_png(images_dir, "diag_y")

    md_path = artifact_dir / "doc.md"
    md_path.write_text(
        "![x](doc.images/diag_x.png)\n![y](doc.images/diag_y.png)\n",
        encoding="utf-8",
    )

    with (
        patch.object(image_postprocess, "classify", side_effect=_classify_all_diagram),
        patch.object(image_postprocess, "png_to_svg", side_effect=_stub_png_to_svg),
    ):
        counts = postprocess(md_path, "doc.images")

    assert counts["visual_dedupe_refs"] == 0
    assert counts["visual_dedupe_files"] == 0

    svg_files = [f for f in images_dir.iterdir() if f.suffix == ".svg"]
    assert len(svg_files) == 2, f"expected 2 separate SVGs, got: {[f.name for f in svg_files]}"

    md_text = md_path.read_text(encoding="utf-8")
    svg_targets = set()
    for line in md_text.splitlines():
        if ".svg)" in line:
            svg_targets.add(line.split("(")[1].rstrip(")"))
    assert len(svg_targets) == 2, f"expected 2 distinct SVG targets, got: {svg_targets}"


# ---------------------------------------------------------------------------
# Unit tests for diagram_phash and _hamming
# ---------------------------------------------------------------------------

def test_phash_near_duplicates_within_threshold(tmp_path):
    logo_a = _make_logo_png(tmp_path, "logo_a", padding=2)
    logo_b = _make_logo_png(tmp_path, "logo_b", padding=6)
    logo_c = _make_logo_png(tmp_path, "logo_c", padding=10)

    hash_a = diagram_phash(logo_a)
    hash_b = diagram_phash(logo_b)
    hash_c = diagram_phash(logo_c)

    assert len(hash_a) == 8  # 64-bit hash
    assert _hamming(hash_a, hash_b) <= DIAGRAM_PHASH_THRESHOLD
    assert _hamming(hash_a, hash_c) <= DIAGRAM_PHASH_THRESHOLD
    assert _hamming(hash_b, hash_c) <= DIAGRAM_PHASH_THRESHOLD


def test_phash_distinct_diagrams_exceed_threshold(tmp_path):
    logo = _make_logo_png(tmp_path, "logo", padding=4)
    distinct = _make_distinct_diagram_png(tmp_path, "distinct")

    assert _hamming(diagram_phash(logo), diagram_phash(distinct)) > DIAGRAM_PHASH_THRESHOLD


def test_phash_identical_images_have_zero_hamming(tmp_path):
    logo = _make_logo_png(tmp_path, "logo", padding=4)
    copy = tmp_path / "logo_copy.png"
    shutil.copy2(logo, copy)

    assert _hamming(diagram_phash(logo), diagram_phash(copy)) == 0
