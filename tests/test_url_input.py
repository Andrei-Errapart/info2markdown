"""Unit tests for URL detection and stem derivation (no network)."""

import pytest

from pdf2markdown import _url_stem, is_url


@pytest.mark.parametrize(
    "value, expected",
    [
        ("https://www.ti.com/lit/ds/symlink/ucc256404.pdf?ts=1", True),
        ("http://example.com/a.pdf", True),
        ("/local/path/a.pdf", False),
        ("a.pdf", False),
        ("./relative/a.pdf", False),
        ("ftp://example.com/a.pdf", False),
        ("C:\\Users\\me\\a.pdf", False),
    ],
)
def test_is_url(value: str, expected: bool) -> None:
    assert is_url(value) is expected


@pytest.mark.parametrize(
    "url, expected",
    [
        # Query string is ignored.
        ("https://www.ti.com/lit/ds/symlink/ucc256404.pdf?ts=123", "ucc256404"),
        ("https://example.com/docs/Datasheet.PDF", "Datasheet"),
        # Percent-encoded path is decoded, then sanitized.
        ("https://example.com/a%20b.pdf", "a_b"),
        # No usable filename -> fallback.
        ("https://example.com/", "document"),
        ("https://example.com", "document"),
    ],
)
def test_url_stem(url: str, expected: str) -> None:
    assert _url_stem(url) == expected
