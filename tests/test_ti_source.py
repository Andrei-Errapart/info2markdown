from datasheet_sources import (
    TIDocumentViewerSource, parse_ti_litnum, parse_ti_guids,
    ti_part_from_url, extract_ti_fragment,
)

LANDING = """
<html><head><script> var litnum = 'SLUSD90E'; </script></head><body>
<ul>
 <li><a href="//www.ti.com/document-viewer/UCC256404/datasheet/GUID-AAA1#T1">1 Features</a></li>
 <li><a href="//www.ti.com/document-viewer/UCC256404/datasheet/GUID-BBB2#T2">2 Apps</a></li>
 <li><a href="//www.ti.com/document-viewer/UCC256404/datasheet/GUID-AAA1#again">dup</a></li>
</ul></body></html>
"""

FRAGMENT = ('<meta name="robots" content="noindex, nofollow" />'
            '<html lang="en-us"><div class="subsection" id="GUID-AAA1">'
            '<h1>Features</h1><p>Low power</p>'
            '<img src="/ods/images/SLUSD90E/GUID-IMG1-low.gif" alt="f"/></div></html>')


def test_parse_ti_litnum():
    assert parse_ti_litnum(LANDING) == "SLUSD90E"
    assert parse_ti_litnum("<html>no litnum</html>") is None


def test_parse_ti_guids_ordered_deduped():
    assert parse_ti_guids(LANDING) == ["GUID-AAA1", "GUID-BBB2"]


def test_ti_part_from_url():
    assert ti_part_from_url("https://www.ti.com/document-viewer/ucc256404/datasheet") == "ucc256404"
    assert ti_part_from_url("https://www.ti.com/document-viewer/UCC256404/datasheet") == "ucc256404"
    assert ti_part_from_url("https://www.ti.com/document-viewer/ja-jp/UCC256404/datasheet") == "ucc256404"


def test_extract_ti_fragment_returns_subsection_div():
    out = extract_ti_fragment(FRAGMENT)
    assert out.startswith('<div class="subsection"')
    assert "Low power" in out
    assert "GUID-IMG1-low.gif" in out
    assert "robots" not in out  # the meta wrapper is dropped


def test_ti_matches():
    s = TIDocumentViewerSource()
    assert s.matches("https://www.ti.com/document-viewer/ucc256404/datasheet")
    assert s.matches("https://www.ti.com/document-viewer/UCC256404/datasheet?x=1")
    assert not s.matches("https://www.ti.com/lit/ds/symlink/ucc256404.pdf")
    assert not s.matches("https://onlinedocs.microchip.com/g/GUID-1")
