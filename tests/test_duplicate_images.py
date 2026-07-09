import hashlib
import json
import shutil
from pathlib import Path

import pytest

from pdf2markdown import (
    convert,
    deduplicate_images,
    split_images,
    update_image_map_for_dedupe,
    update_image_map_from_markdown,
)
from tests.fixtures.generate_duplicate_fixture import STEM, make_png, write_fixture


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _embedded_image(alt: str, data: bytes) -> str:
    import base64

    encoded = base64.b64encode(data).decode("ascii")
    return f"![{alt}](data:image/png;base64,{encoded})"


def test_split_images_captures_image_map_entries_in_markdown_order(tmp_path: Path) -> None:
    first = make_png(12, 12, [(10, 20, 30)])
    second = make_png(12, 12, [(40, 50, 60)])
    embedded_md = tmp_path / "doc.embedded.md"
    embedded_md.write_text(
        "\n".join([
            "# Doc",
            _embedded_image("first alt", first),
            _embedded_image("second alt", second),
        ]),
        encoding="utf-8",
    )
    out_md = tmp_path / "doc.md"
    images_dir = tmp_path / "doc.images"
    entries = []

    extracted = split_images(embedded_md, out_md, images_dir, image_entries=entries)

    assert extracted == 2
    assert [entry["occurrence"] for entry in entries] == [1, 2]
    assert [entry["alt"] for entry in entries] == ["first alt", "second alt"]
    assert entries[0]["original_file"].startswith("image_001_")
    assert entries[1]["original_file"].startswith("image_002_")
    assert entries[0]["final_file"] == entries[0]["original_file"]
    assert entries[1]["content_sha256"] == hashlib.sha256(second).hexdigest()
    assert "doc.images/image_001_" in out_md.read_text(encoding="utf-8")


def test_dedupe_updates_image_map_but_keeps_duplicate_occurrences(tmp_path: Path) -> None:
    data = make_png(12, 12, [(10, 20, 30), (240, 240, 240)])
    embedded_md = tmp_path / "doc.embedded.md"
    embedded_md.write_text(
        "\n".join([
            _embedded_image("one", data),
            _embedded_image("two", data),
        ]),
        encoding="utf-8",
    )
    out_md = tmp_path / "doc.md"
    images_dir = tmp_path / "doc.images"
    entries = []
    split_images(embedded_md, out_md, images_dir, image_entries=entries)

    stats = deduplicate_images(out_md, images_dir.name)
    update_image_map_for_dedupe(entries, stats)

    canonical = f"{hashlib.sha256(data).hexdigest()}.png"
    assert [entry["occurrence"] for entry in entries] == [1, 2]
    assert [entry["final_file"] for entry in entries] == [canonical, canonical]
    assert [entry["status"] for entry in entries] == ["kept", "deduped"]


def test_update_image_map_from_markdown_detects_vectorized_refs(tmp_path: Path) -> None:
    images_dir = tmp_path / "doc.images"
    images_dir.mkdir()
    (images_dir / "diagram.svg").write_text("<svg></svg>", encoding="utf-8")
    md_path = tmp_path / "doc.md"
    md_path.write_text("![diagram](doc.images/diagram.svg)\n", encoding="utf-8")
    entries = [{
        "occurrence": 1,
        "alt": "diagram",
        "original_file": "diagram.png",
        "final_file": "diagram.png",
        "status": "kept",
        "page": None,
        "bbox": None,
        "coord_system": None,
        "content_sha256": "abc",
    }]

    update_image_map_from_markdown(entries, md_path, images_dir.name)

    assert entries[0]["final_file"] == "diagram.svg"
    assert entries[0]["status"] == "vectorized"


def test_update_image_map_from_markdown_marks_removed_refs_inlined(tmp_path: Path) -> None:
    images_dir = tmp_path / "doc.images"
    images_dir.mkdir()
    (images_dir / "kept.png").write_bytes(make_png(12, 12, [(40, 50, 60)]))
    md_path = tmp_path / "doc.md"
    md_path.write_text("inline text\n\n![kept](doc.images/kept.png)\n", encoding="utf-8")
    entries = [
        {
            "occurrence": 1,
            "alt": "text",
            "original_file": "text.png",
            "final_file": "text.png",
            "status": "kept",
            "page": None,
            "bbox": None,
            "coord_system": None,
            "content_sha256": "abc",
        },
        {
            "occurrence": 2,
            "alt": "kept",
            "original_file": "kept.png",
            "final_file": "kept.png",
            "status": "kept",
            "page": None,
            "bbox": None,
            "coord_system": None,
            "content_sha256": "def",
        },
    ]

    update_image_map_from_markdown(entries, md_path, images_dir.name)

    assert entries[0]["final_file"] is None
    assert entries[0]["status"] == "inlined"
    assert entries[1]["final_file"] == "kept.png"
    assert entries[1]["status"] == "kept"


def test_convert_writes_image_map_and_protects_it(tmp_path: Path, monkeypatch) -> None:
    data = make_png(12, 12, [(10, 20, 30)])
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 fake")

    def fake_run_docling(source: Path, out_dir: Path, ocr: bool):
        embedded = out_dir / "doc.md"
        embedded.write_text(_embedded_image("from docling", data), encoding="utf-8")
        return embedded, [{
            "alt": "metadata alt",
            "page": 7,
            "bbox": {"l": 1.0, "t": 2.0, "r": 3.0, "b": 4.0},
            "coord_system": "docling",
        }]

    monkeypatch.setattr("pdf2markdown.run_docling", fake_run_docling)
    out_dir = tmp_path / "out"

    _, out_md, extracted, _ = convert(src, out_dir, ocr=False, force=False, postprocess=False)

    image_map = out_dir / "doc.image-map.json"
    payload = json.loads(image_map.read_text(encoding="utf-8"))
    assert extracted == 1
    assert payload["source"] == "doc.pdf"
    assert payload["markdown"] == out_md.name
    assert payload["images_dir"] == "doc.images"
    assert payload["entries"][0]["alt"] == "metadata alt"
    assert payload["entries"][0]["page"] == 7
    assert payload["entries"][0]["bbox"] == {"l": 1.0, "t": 2.0, "r": 3.0, "b": 4.0}

    out_md.unlink()
    shutil.rmtree(out_dir / "doc.images")
    with pytest.raises(SystemExit, match="image-map"):
        convert(src, out_dir, ocr=False, force=False, postprocess=False)

    image_map.write_text('{"old": true}\n', encoding="utf-8")
    convert(src, out_dir, ocr=False, force=True, postprocess=False)
    assert "old" not in image_map.read_text(encoding="utf-8")


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
