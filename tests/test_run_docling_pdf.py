"""The PDF route converts via docling's Python API and serialises embedded MD."""

import pdf2markdown


def test_run_docling_pdf_serializes_embedded_markdown(tmp_path, monkeypatch):
    class FakeDoc:
        def export_to_markdown(self, image_mode=None):
            return "# hi\n\n$$x=1$$\n"

    class FakeResult:
        document = FakeDoc()

    class FakeConv:
        def convert(self, src, **kw):
            return FakeResult()

    monkeypatch.setattr(pdf2markdown, "build_pdf_converter", lambda ocr: FakeConv())
    src = tmp_path / "d.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    out, metadata = pdf2markdown.run_docling_pdf(src, tmp_path, ocr=True)
    assert out == tmp_path / "d.md"
    assert metadata == []
    assert "$$x=1$$" in out.read_text()
