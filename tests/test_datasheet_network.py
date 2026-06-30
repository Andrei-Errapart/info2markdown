# tests/test_datasheet_network.py
"""Opt-in network tests. Run with: pytest -m slow tests/test_datasheet_network.py"""
import pytest
from pathlib import Path
from datasheet_sources import TIDocumentViewerSource, MicrochipOnlineDocsSource

pytestmark = pytest.mark.slow


def test_ti_fetch_real(tmp_path: Path):
    html, stem = TIDocumentViewerSource().fetch(
        "https://www.ti.com/document-viewer/ucc256404/datasheet", tmp_path)
    text = html.read_text(encoding="utf-8")
    assert stem == "ucc256404"
    assert "<table" in text          # the HTML route preserves real tables
    assert text.count("<h1") >= 5     # multiple section headings


def test_microchip_fetch_real(tmp_path: Path):
    html, stem = MicrochipOnlineDocsSource().fetch(
        "https://onlinedocs.microchip.com/g/GUID-3EE676DF-490E-41BC-98F0-5774B35DC989",
        tmp_path)
    text = html.read_text(encoding="utf-8")
    assert stem  # non-empty (e.g. "AVR-DA-Family")
    assert text.count("<article") >= 10  # many topics concatenated
