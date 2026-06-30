from datasheet_sources import find_source


def test_find_source_routes_ti():
    s = find_source("https://www.ti.com/document-viewer/ucc256404/datasheet")
    assert s is not None and s.name == "ti"


def test_find_source_routes_microchip():
    s = find_source("https://onlinedocs.microchip.com/g/GUID-3EE676DF-1-1-1-1")
    assert s is not None and s.name == "microchip"


def test_find_source_returns_none_for_pdf_or_unknown():
    assert find_source("https://www.ti.com/lit/ds/symlink/ucc256404.pdf") is None
    assert find_source("https://example.com/whatever") is None
