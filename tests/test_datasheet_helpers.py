import base64
from datasheet_sources import (
    inline_images, build_html_document, sanitize_stem,
)


def test_inline_images_rewrites_src_to_data_uri():
    html = '<div><img src="/ods/images/X/GUID-1-low.gif" alt="f"></div>'
    captured = {}

    def fake_fetch(url):
        captured["url"] = url
        return b"GIFBYTES"

    out = inline_images(html, "https://www.ti.com/", fetch_bytes=fake_fetch)
    assert captured["url"] == "https://www.ti.com/ods/images/X/GUID-1-low.gif"
    assert 'src="data:image/gif;base64,' in out
    assert base64.b64encode(b"GIFBYTES").decode() in out
    assert 'alt="f"' in out


def test_inline_images_skips_data_uri_and_missing_src():
    html = '<img src="data:image/png;base64,AAAA"><img alt="no-src">'
    out = inline_images(html, "https://x/", fetch_bytes=lambda u: b"X")
    assert out.count("data:image/png;base64,AAAA") == 1


def test_inline_images_leaves_img_when_fetch_returns_none():
    html = '<img src="rel/pic.png">'
    out = inline_images(html, "https://x/docs/", fetch_bytes=lambda u: None)
    assert 'src="https://x/docs/rel/pic.png"' in out or 'src="rel/pic.png"' in out
    assert "data:" not in out


def test_build_html_document_wraps_body():
    doc = build_html_document("My Part", "<h1>Hi</h1>")
    assert doc.startswith("<!DOCTYPE html>")
    assert "<title>My Part</title>" in doc
    assert "<body><h1>Hi</h1></body>" in doc


def test_sanitize_stem():
    assert sanitize_stem("AVR® DA Family") == "AVR-DA-Family"
    assert sanitize_stem("  weird/name:v2 ") == "weird-name-v2"
    assert sanitize_stem("") == "document"
    assert sanitize_stem("///") == "document"


def test_build_html_document_escapes_title():
    doc = build_html_document("LM324 <Rev B> & C", "<p>x</p>")
    assert "<title>LM324 &lt;Rev B&gt; &amp; C</title>" in doc
    assert "<p>x</p>" in doc  # body stays raw
