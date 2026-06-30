import pdf2markdown as p


def test_looks_like_pdf():
    assert p._looks_like_pdf("https://www.ti.com/lit/ds/symlink/ucc256404.pdf?ts=1")
    assert p._looks_like_pdf("https://x/a.PDF")
    assert not p._looks_like_pdf("https://onlinedocs.microchip.com/g/GUID-1")
    assert not p._looks_like_pdf("https://www.ti.com/document-viewer/ucc256404/datasheet")


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


def test_main_unsupported_url_errors(monkeypatch):
    monkeypatch.setattr(p, "find_source", lambda url: None)
    monkeypatch.setattr(p.sys, "argv", ["pdf2markdown", "https://example.com/page.html"])
    try:
        p.main()
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert "unsupported URL" in str(e)
