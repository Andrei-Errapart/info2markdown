from pathlib import Path
from pdf2markdown import build_docling_cmd


def test_pdf_cmd_includes_ocr_and_tables():
    cmd = build_docling_cmd("docling", Path("/tmp/a.pdf"), Path("/out"), ocr=True)
    assert "--ocr" in cmd
    assert "--tables" in cmd and "accurate" in cmd
    assert "--enrich-formula" in cmd   # PDF equations are vector/text -> LaTeX
    assert cmd[1] == "/tmp/a.pdf"
    assert "embedded" in cmd


def test_pdf_cmd_without_ocr():
    cmd = build_docling_cmd("docling", Path("/tmp/a.pdf"), Path("/out"), ocr=False)
    assert "--ocr" not in cmd
    assert "--tables" in cmd


def test_html_cmd_has_no_ocr_or_tables():
    cmd = build_docling_cmd("docling", Path("/tmp/a.html"), Path("/out"), ocr=True)
    assert "--ocr" not in cmd
    assert "--tables" not in cmd
    assert "--enrich-formula" not in cmd   # HTML equations handled via LaTeX-OCR
    assert "--image-export-mode" in cmd and "embedded" in cmd
    assert "--output" in cmd and "/out" in cmd
