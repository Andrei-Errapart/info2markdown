import hashlib
import json
import shutil
from pathlib import Path

import pytest

from pdf2markdown import convert, deduplicate_images, split_images
from tests.fixtures.generate_duplicate_fixture import STEM, make_png, write_fixture


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_split_images_then_dedupe_matches_generated_expected_markdown(tmp_path: Path) -> None:
    paths = write_fixture(tmp_path, pdf=False)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    out_md = tmp_path / f"{STEM}.md"
    images_dir = tmp_path / f"{STEM}.images"

    extracted = split_images(paths["embedded_md"], out_md, images_dir)
    stats = deduplicate_images(out_md, images_dir.name)

    assert extracted == manifest["source_image_refs"]
    assert out_md.read_text(encoding="utf-8") == paths["expected_md"].read_text(encoding="utf-8")
    assert sorted(p.name for p in images_dir.iterdir()) == manifest["expected_files"]
    assert stats["hash_algorithm"] == manifest["hash_algorithm"]
    assert stats["unique_images"] == manifest["unique_images"]
    assert stats["duplicate_refs"] == manifest["duplicate_refs"]
    assert stats["canonical_by_original"] == manifest["canonical_by_original"]
    assert stats["duplicate_groups"] == manifest["duplicate_groups"]

    for image_path in images_dir.iterdir():
        assert image_path.stem == sha256(image_path)


def test_dedupe_canonicalizes_unique_images_without_removing_files(tmp_path: Path) -> None:
    images_dir = tmp_path / "doc.images"
    images_dir.mkdir()
    first = images_dir / "image_001_a.png"
    second = images_dir / "image_002_b.png"
    first.write_bytes(make_png(12, 12, [(10, 20, 30)]))
    second.write_bytes(make_png(12, 12, [(40, 50, 60)]))
    md_path = tmp_path / "doc.md"
    md_path.write_text(
        "![one](doc.images/image_001_a.png)\n![two](doc.images/image_002_b.png)\n",
        encoding="utf-8",
    )

    stats = deduplicate_images(md_path, images_dir.name)

    assert stats["duplicate_refs"] == 0
    assert stats["removed_files"] == 0
    assert sorted(p.name for p in images_dir.iterdir()) == sorted(
        stats["canonical_by_original"].values()
    )
    assert "image_001_a.png" not in md_path.read_text(encoding="utf-8")
    assert "image_002_b.png" not in md_path.read_text(encoding="utf-8")


def test_dedupe_rewrites_duplicate_refs_and_removes_duplicate_files(tmp_path: Path) -> None:
    images_dir = tmp_path / "doc.images"
    images_dir.mkdir()
    data = make_png(12, 12, [(10, 20, 30), (240, 240, 240)])
    (images_dir / "image_001_a.png").write_bytes(data)
    (images_dir / "image_002_a.png").write_bytes(data)
    md_path = tmp_path / "doc.md"
    md_path.write_text(
        "![one](doc.images/image_001_a.png)\n![two](doc.images/image_002_a.png)\n",
        encoding="utf-8",
    )

    stats = deduplicate_images(md_path, images_dir.name)
    canonical = f"{hashlib.sha256(data).hexdigest()}.png"

    assert stats["duplicate_refs"] == 1
    assert stats["removed_files"] == 1
    assert [p.name for p in images_dir.iterdir()] == [canonical]
    assert md_path.read_text(encoding="utf-8").count(f"doc.images/{canonical}") == 2


def test_dedupe_leaves_external_and_missing_refs_unchanged(tmp_path: Path) -> None:
    images_dir = tmp_path / "doc.images"
    images_dir.mkdir()
    md_path = tmp_path / "doc.md"
    md_path.write_text(
        "![external](other.images/image_001.png)\n"
        "![missing](doc.images/missing.png)\n",
        encoding="utf-8",
    )

    stats = deduplicate_images(md_path, images_dir.name)

    assert stats["missing_files"] == 1
    assert stats["unique_images"] == 0
    assert md_path.read_text(encoding="utf-8") == (
        "![external](other.images/image_001.png)\n"
        "![missing](doc.images/missing.png)\n"
    )
    assert not images_dir.exists() or list(images_dir.iterdir()) == []


@pytest.mark.e2e
@pytest.mark.slow
def test_generated_pdf_conversion_keeps_only_unique_image_content(tmp_path: Path) -> None:
    if shutil.which("docling") is None and not (Path(__import__("sys").executable).parent / "docling").exists():
        pytest.skip("docling is not available")
    try:
        paths = write_fixture(tmp_path / "fixture", pdf=True)
    except ModuleNotFoundError as exc:
        if exc.name == "reportlab":
            pytest.skip("reportlab is not available")
        raise

    _, out_md, extracted, stats = convert(
        paths["pdf"],
        tmp_path / "out",
        ocr=False,
        force=True,
        postprocess=False,
    )
    images_dir = out_md.parent / f"{out_md.stem}.images"
    if not images_dir.is_dir():
        pytest.skip("docling did not emit embedded images for this synthetic PDF")

    hashes = [sha256(path) for path in images_dir.iterdir() if path.is_file()]
    assert extracted >= len(hashes)
    assert len(hashes) == len(set(hashes))
    assert stats["dedupe"]["removed_files"] == max(extracted - len(hashes), 0)

    md_text = out_md.read_text(encoding="utf-8")
    for path in images_dir.iterdir():
        assert path.name in md_text
