import pdf2markdown as p


def test_main_routes_datasheet_url_through_source(monkeypatch, tmp_path):
    calls = {}

    class FakeSource:
        name = "fake"
        def fetch(self, url, work_dir):
            f = work_dir / "part.html"
            f.write_text("<html></html>", encoding="utf-8")
            calls["fetched"] = url
            return f, "part"

    monkeypatch.setattr(p, "find_source", lambda url: FakeSource())

    def fake_convert(source, output_dir, ocr, force, postprocess):
        calls["source_suffix"] = source.suffix
        calls["output_dir"] = output_dir
        return source, output_dir / "part.md", 0, {}

    monkeypatch.setattr(p, "convert", fake_convert)
    monkeypatch.setattr(p.sys, "argv",
                        ["pdf2markdown", "https://www.ti.com/document-viewer/ucc256404/datasheet"])
    monkeypatch.chdir(tmp_path)

    assert p.main() == 0
    assert calls["fetched"].endswith("/datasheet")
    assert calls["source_suffix"] == ".html"
    assert calls["output_dir"] == tmp_path  # URL default output = cwd


def test_main_routes_non_datasheet_url_to_download_pdf(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(p, "find_source", lambda url: None)

    def fake_download_pdf(url, work_dir):
        calls["url"] = url
        f = work_dir / "doc.pdf"
        f.write_text("%PDF-1.4", encoding="utf-8")
        return f

    monkeypatch.setattr(p, "download_pdf", fake_download_pdf)

    def fake_convert(source, output_dir, ocr, force, postprocess):
        calls["suffix"] = source.suffix
        return source, output_dir / "doc.md", 0, {}

    monkeypatch.setattr(p, "convert", fake_convert)
    monkeypatch.setattr(p.sys, "argv", ["pdf2markdown", "https://example.com/download?id=123"])
    monkeypatch.chdir(tmp_path)

    assert p.main() == 0
    assert calls["url"] == "https://example.com/download?id=123"
    assert calls["suffix"] == ".pdf"
