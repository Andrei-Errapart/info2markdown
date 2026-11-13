"""convert() keeps the source file name, extension case included."""

from pathlib import Path

import pytest

import pdf2markdown


@pytest.fixture
def fake_docling(monkeypatch):
    def run_docling(source, tmp_dir, ocr):
        embedded = Path(tmp_dir) / f"{source.stem}.md"
        embedded.write_text("# Title\n\nText.\n", encoding="utf-8")
        return embedded, []

    monkeypatch.setattr(pdf2markdown, "run_docling", run_docling)


def convert(source, output_dir):
    return pdf2markdown.convert(source, output_dir, ocr=False, force=False,
                                postprocess=False)


def test_uppercase_extension_in_place_adds_no_lowercase_copy(tmp_path, fake_docling):
    source = tmp_path / "DOC.PDF"
    source.write_bytes(b"%PDF-1.4 fake")

    source_copy, out_md, _, _ = convert(source, tmp_path)

    assert source_copy == source
    assert out_md == tmp_path / "DOC.md"
    assert sorted(p.name for p in tmp_path.glob("DOC.*") if p.is_file()) == [
        "DOC.PDF", "DOC.image-map.json", "DOC.md"]


def test_copy_to_other_dir_keeps_extension_case(tmp_path, fake_docling):
    source = tmp_path / "in" / "DOC.PDF"
    source.parent.mkdir()
    source.write_bytes(b"%PDF-1.4 fake")

    source_copy, _, _, _ = convert(source, tmp_path / "out")

    assert source_copy == tmp_path / "out" / "DOC.PDF"
    assert source_copy.read_bytes() == source.read_bytes()
