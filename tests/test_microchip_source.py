from datasheet_sources import (
    MicrochipOnlineDocsSource, parse_oxy_toc, extract_oxy_topic,
    oxy_doc_title, oxy_root_guid,
)

INDEX = """
<html><head><title>AVR® DA Family</title></head><body>
<nav id="wh_publication_toc"><ul>
 <li><a href="GUID-111.html" data-id="GUID-111">Intro</a></li>
 <li><a href="GUID-222.html" data-id="GUID-222">Overview</a></li>
 <li><a href="GUID-111.html">dup</a></li>
 <li><a href="https://example.com/external.html">ext</a></li>
 <li><a href="oxygen-webhelp/app/main.js">asset</a></li>
</ul></nav></body></html>
"""

TOPIC = """
<!DOCTYPE html><html><head><title>Introduction</title></head><body>
<header class="wh_header">CHROME</header>
<nav id="wh_publication_toc">TOC CHROME</nav>
<article role="article">
 <h1 class="title topictitle1">Introduction</h1>
 <p>The AVR128DA microcontrollers...</p>
 <img src="GUID-IMG2.png" alt="block"/>
</article>
<footer class="wh_footer">FOOT</footer></body></html>
"""


def test_parse_oxy_toc_ordered_deduped_filtered():
    assert parse_oxy_toc(INDEX) == ["GUID-111.html", "GUID-222.html"]


def test_parse_oxy_toc_ignores_links_outside_toc_nav():
    html = """
    <html><body>
    <div class="wh_breadcrumb"><a href="GUID-999.html">crumb</a></div>
    <nav id="wh_publication_toc"><ul>
      <li><a href="GUID-111.html">Intro</a></li>
      <li><a href="GUID-222.html">Overview</a></li>
    </ul></nav>
    <a href="GUID-888.html">next-button</a>
    </body></html>
    """
    assert parse_oxy_toc(html) == ["GUID-111.html", "GUID-222.html"]


def test_extract_oxy_topic_returns_article_only():
    out = extract_oxy_topic(TOPIC)
    assert out.startswith("<article")
    assert "Introduction" in out and "AVR128DA microcontrollers" in out
    assert "GUID-IMG2.png" in out
    assert "CHROME" not in out and "FOOT" not in out


def test_oxy_doc_title():
    assert oxy_doc_title(INDEX) == "AVR® DA Family"
    assert oxy_doc_title("<html><head></head></html>") is None


def test_oxy_root_guid():
    u = "https://onlinedocs.microchip.com/oxy/GUID-3EE676DF-490E-41BC-98F0-5774B35DC989-en-US-25/index.html"
    assert oxy_root_guid(u) == "GUID-3EE676DF-490E-41BC-98F0-5774B35DC989"
    short = "https://onlinedocs.microchip.com/g/GUID-3EE676DF-490E-41BC-98F0-5774B35DC989"
    assert oxy_root_guid(short) == "GUID-3EE676DF-490E-41BC-98F0-5774B35DC989"


def test_microchip_matches():
    s = MicrochipOnlineDocsSource()
    assert s.matches("https://onlinedocs.microchip.com/g/GUID-1")
    assert s.matches("https://onlinedocs.microchip.com/oxy/GUID-1-en-US-25/index.html")
    assert not s.matches("https://www.ti.com/document-viewer/ucc256404/datasheet")
    assert not s.matches("https://ww1.microchip.com/downloads/x.pdf")


def test_microchip_fetch_builds_topic_urls_and_concatenates(monkeypatch, tmp_path):
    import datasheet_sources as ds
    monkeypatch.setattr(
        ds, "fetch_text_and_url",
        lambda url, *a, **k: (INDEX, "https://onlinedocs.microchip.com/oxy/GUID-ROOT-en-US-25/index.html"))
    captured = {}

    def fake_parallel(urls, *a, **k):
        captured["urls"] = list(urls)
        return {u: '<article role="article"><h1>T</h1><p>x</p></article>' for u in urls}

    monkeypatch.setattr(ds, "parallel_fetch_text", fake_parallel)
    out, stem = ds.MicrochipOnlineDocsSource().fetch(
        "https://onlinedocs.microchip.com/g/GUID-ROOT", tmp_path)
    assert stem == "AVR-DA-Family"
    assert captured["urls"] == [
        "https://onlinedocs.microchip.com/oxy/GUID-ROOT-en-US-25/GUID-111.html",
        "https://onlinedocs.microchip.com/oxy/GUID-ROOT-en-US-25/GUID-222.html",
    ]
    assert out.read_text(encoding="utf-8").count("<article") == 2
