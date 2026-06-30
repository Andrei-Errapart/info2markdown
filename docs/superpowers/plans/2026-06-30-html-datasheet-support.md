# HTML Datasheet Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert vendor HTML datasheets (TI document-viewer, Microchip onlinedocs) to Markdown through the existing `pdf2markdown` tool, via a vendor-pluggable source abstraction.

**Architecture:** A new module `scripts/datasheet_sources.py` defines a `DatasheetSource` interface plus TI and Microchip adapters. Each adapter fetches a vendor's GUID-based HTML and emits **one self-contained `.html` file with images inlined as base64 data-URIs**. `pdf2markdown.py` detects a datasheet URL, calls the matching adapter, then runs the existing `docling → split_images → deduplicate_images → image_postprocess` pipeline unchanged (the HTML and PDF routes converge right after docling, because docling's embedded mode already emits base64 data-URIs that `split_images()` consumes).

**Tech Stack:** Python 3 (stdlib `urllib` for HTTP), `beautifulsoup4` for HTML parsing, `docling` for conversion, `pytest` for tests.

## Global Constraints

- HTTP uses stdlib `urllib.request` only — no new HTTP dependency. Browser `User-Agent` (`"Mozilla/5.0 (compatible; pdf2markdown)"`) and a `certifi`-backed SSL context (guarded import; falls back to `ssl.create_default_context()`).
- New direct import `beautifulsoup4` MUST be pinned in `scripts/requirements.txt` (repo convention: pin anything imported directly even if docling pulls it transitively).
- Fatal errors use `raise SystemExit(f"Error: ...")` (no traceback). Per-item (section/topic/image) failures degrade gracefully: skip + `print(..., file=sys.stderr)`, never abort the whole document.
- Tests run under `pytest` with `pythonpath = scripts` (already set in `pytest.ini`). Unit tests MUST NOT hit the network — use inline HTML fixtures and injected fetchers. Network tests are marked `@pytest.mark.slow` and are opt-in.
- Output stem: TI → part number from URL (lowercased); Microchip → sanitized `<title>`, fallback root GUID. Default `output_dir` for any URL input → current working directory.
- Polite network behavior: parallel fetches capped at 6 concurrent workers with per-request timeouts.

---

## File Structure

- **Create** `scripts/datasheet_sources.py` — source interface, registry, shared HTTP/parse/inline helpers, TI + Microchip adapters.
- **Modify** `scripts/pdf2markdown.py` — generalize `run_docling()`/`convert()` for `.html`; 3-way URL dispatch in `main()`.
- **Modify** `scripts/requirements.txt` — add `beautifulsoup4`.
- **Modify** `scripts/pdf2markdown` (bash wrapper) — add `bs4` to the venv import probe; update usage comment.
- **Modify** `CLAUDE.md` — document HTML datasheet support.
- **Create** `tests/test_datasheet_helpers.py`, `tests/test_ti_source.py`, `tests/test_microchip_source.py`, `tests/test_source_registry.py`, `tests/test_docling_cmd.py`, `tests/test_url_dispatch.py`.

The current `convert()`/`run_docling()`/`main()` live in `scripts/pdf2markdown.py` (≈342 lines pre-change). The `download_pdf`, `is_url`, `_url_stem` helpers already exist there (commit `f6b47cf`) and are reused/left intact.

---

## Task 1: Dependency + shared helpers in `datasheet_sources.py`

**Files:**
- Create: `scripts/datasheet_sources.py`
- Modify: `scripts/requirements.txt`
- Modify: `scripts/pdf2markdown` (bash wrapper, import probe line)
- Test: `tests/test_datasheet_helpers.py`

**Interfaces:**
- Produces:
  - `fetch_bytes(url: str, timeout: int = 60) -> bytes` (raises `SystemExit` on failure)
  - `fetch_text(url: str, timeout: int = 60) -> str` (raises)
  - `fetch_text_and_url(url: str, timeout: int = 60) -> tuple[str, str]` (text, final-redirected-URL; raises)
  - `try_fetch_text(url: str, timeout: int = 30) -> str | None`
  - `try_fetch_bytes(url: str, timeout: int = 30) -> bytes | None`
  - `parallel_fetch_text(urls: list[str], max_workers: int = 6, timeout: int = 30) -> dict[str, str | None]`
  - `inline_images(html: str, base_url: str, fetch_bytes=try_fetch_bytes) -> str`
  - `build_html_document(title: str, body_html: str) -> str`
  - `sanitize_stem(text: str) -> str`
  - `class DatasheetSource(ABC)` with `name: str`, `matches(url)->bool`, `fetch(url, work_dir)->tuple[Path,str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_datasheet_helpers.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_datasheet_helpers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'datasheet_sources'`

- [ ] **Step 3: Add the dependency**

Append to `scripts/requirements.txt`:

```
beautifulsoup4>=4.12.0
```

In `scripts/pdf2markdown`, add `bs4` to the venv import probe so existing venvs re-provision. Change the line:

```bash
   "$VENV/bin/python" -c 'import cv2, numpy, scipy, PIL, rapidocr, onnxruntime, vtracer' 2>/dev/null; then
```
to:
```bash
   "$VENV/bin/python" -c 'import cv2, numpy, scipy, PIL, rapidocr, onnxruntime, vtracer, bs4' 2>/dev/null; then
```

- [ ] **Step 4: Write the module with shared helpers**

```python
# scripts/datasheet_sources.py
"""Vendor-pluggable HTML datasheet sources.

Each DatasheetSource fetches a vendor's GUID-based HTML datasheet and produces
ONE self-contained .html file (images inlined as base64 data-URIs), which the
existing pdf2markdown pipeline then converts via docling.
"""

import base64
import concurrent.futures
import re
import ssl
import sys
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

try:
    import certifi
    _CERTIFI = True
except ImportError:
    _CERTIFI = False

_UA = "Mozilla/5.0 (compatible; pdf2markdown)"

_MIME = {
    ".gif": "image/gif", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".webp": "image/webp",
}


def _ssl_context() -> ssl.SSLContext:
    if _CERTIFI:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def _request(url: str, timeout: int) -> Tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        return resp.read(), resp.geturl()


def fetch_bytes(url: str, timeout: int = 60) -> bytes:
    try:
        return _request(url, timeout)[0]
    except urllib.error.URLError as exc:
        raise SystemExit(f"Error: failed to fetch {url}: {getattr(exc, 'reason', exc)}")


def fetch_text(url: str, timeout: int = 60) -> str:
    return fetch_bytes(url, timeout).decode("utf-8", errors="replace")


def fetch_text_and_url(url: str, timeout: int = 60) -> Tuple[str, str]:
    try:
        raw, final = _request(url, timeout)
    except urllib.error.URLError as exc:
        raise SystemExit(f"Error: failed to fetch {url}: {getattr(exc, 'reason', exc)}")
    return raw.decode("utf-8", errors="replace"), final


def try_fetch_bytes(url: str, timeout: int = 30) -> Optional[bytes]:
    try:
        return _request(url, timeout)[0]
    except (urllib.error.URLError, ValueError):
        print(f"Warning: failed to fetch {url}", file=sys.stderr)
        return None


def try_fetch_text(url: str, timeout: int = 30) -> Optional[str]:
    raw = try_fetch_bytes(url, timeout)
    return raw.decode("utf-8", errors="replace") if raw is not None else None


def parallel_fetch_text(urls: List[str], max_workers: int = 6,
                        timeout: int = 30) -> Dict[str, Optional[str]]:
    results: Dict[str, Optional[str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(try_fetch_text, u, timeout): u for u in urls}
        for fut in concurrent.futures.as_completed(futs):
            results[futs[fut]] = fut.result()
    return results


def inline_images(html: str, base_url: str,
                  fetch_bytes: Callable[[str], Optional[bytes]] = try_fetch_bytes) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        src = img.get("src")
        if not src or src.startswith("data:"):
            continue
        abs_url = urljoin(base_url, src)
        raw = fetch_bytes(abs_url)
        if raw is None:
            img["src"] = abs_url  # leave a resolved (if dead) link rather than abort
            continue
        ext = Path(urlsplit(abs_url).path).suffix.lower()
        mime = _MIME.get(ext, "image/png")
        b64 = base64.b64encode(raw).decode("ascii")
        img["src"] = f"data:{mime};base64,{b64}"
    return str(soup)


def build_html_document(title: str, body_html: str) -> str:
    return ('<!DOCTYPE html><html><head><meta charset="utf-8">'
            f"<title>{title}</title></head><body>{body_html}</body></html>")


def sanitize_stem(text: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", (text or "").strip()).strip("-._")
    return stem or "document"


class DatasheetSource(ABC):
    name: str = "datasheet"

    @abstractmethod
    def matches(self, url: str) -> bool: ...

    @abstractmethod
    def fetch(self, url: str, work_dir: Path) -> Tuple[Path, str]:
        """Return (path-to-self-contained-html, output-stem)."""
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_datasheet_helpers.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add scripts/datasheet_sources.py scripts/requirements.txt scripts/pdf2markdown tests/test_datasheet_helpers.py
git commit -m "Add datasheet_sources module with shared HTTP/parse helpers"
```

---

## Task 2: TI document-viewer adapter

**Files:**
- Modify: `scripts/datasheet_sources.py`
- Test: `tests/test_ti_source.py`

**Interfaces:**
- Consumes (Task 1): `fetch_text`, `parallel_fetch_text`, `inline_images`, `build_html_document`, `try_fetch_bytes`, `DatasheetSource`.
- Produces:
  - `parse_ti_litnum(html: str) -> Optional[str]`
  - `parse_ti_guids(html: str) -> List[str]` (document order, de-duplicated)
  - `ti_part_from_url(url: str) -> str`
  - `extract_ti_fragment(fragment_html: str) -> str`
  - `class TIDocumentViewerSource(DatasheetSource)` with `name = "ti"`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ti_source.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ti_source.py -v`
Expected: FAIL with `ImportError: cannot import name 'TIDocumentViewerSource'`

- [ ] **Step 3: Implement the TI adapter**

Append to `scripts/datasheet_sources.py`:

```python
_TI_GUID_RE = re.compile(r"/document-viewer/[^/\"']+/datasheet/(GUID-[A-Fa-f0-9-]+)")
_TI_PART_RE = re.compile(r"/document-viewer/(?:[a-z]{2}-[a-z]{2}/)?([^/]+)/datasheet", re.I)


def parse_ti_litnum(html: str) -> Optional[str]:
    m = re.search(r"litnum\s*=\s*'([^']+)'", html)
    return m.group(1) if m else None


def parse_ti_guids(html: str) -> List[str]:
    out: List[str] = []
    seen = set()
    for g in _TI_GUID_RE.findall(html):
        key = g.upper()
        if key not in seen:
            seen.add(key)
            out.append(g)
    return out


def ti_part_from_url(url: str) -> str:
    m = _TI_PART_RE.search(urlsplit(url).path)
    return m.group(1).lower() if m else "datasheet"


def extract_ti_fragment(fragment_html: str) -> str:
    soup = BeautifulSoup(fragment_html, "html.parser")
    div = soup.find("div", class_="subsection") or soup.find("div", class_="section")
    return str(div) if div else ""


class TIDocumentViewerSource(DatasheetSource):
    name = "ti"

    def matches(self, url: str) -> bool:
        p = urlsplit(url)
        return (p.netloc.lower().endswith("ti.com")
                and re.search(r"/document-viewer/[^/]+.*?/datasheet", p.path, re.I) is not None)

    def fetch(self, url: str, work_dir: Path) -> Tuple[Path, str]:
        landing = fetch_text(url)
        guids = parse_ti_guids(landing)
        if not guids:
            raise SystemExit(f"Error: no datasheet sections found at {url}")
        part = ti_part_from_url(url)
        base = url.split("?", 1)[0].rstrip("/")
        section_urls = [f"{base}/{g}?raw=1" for g in guids]
        fetched = parallel_fetch_text(section_urls)
        bodies = []
        for su in section_urls:
            html = fetched.get(su)
            if not html:
                continue
            frag = extract_ti_fragment(html)
            if frag:
                bodies.append(inline_images(frag, "https://www.ti.com/"))
        if not bodies:
            raise SystemExit(f"Error: no section content retrieved for {url}")
        out = work_dir / f"{part}.html"
        out.write_text(build_html_document(part, "\n".join(bodies)), encoding="utf-8")
        return out, part
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ti_source.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/datasheet_sources.py tests/test_ti_source.py
git commit -m "Add TI document-viewer datasheet source"
```

---

## Task 3: Microchip onlinedocs adapter

**Files:**
- Modify: `scripts/datasheet_sources.py`
- Test: `tests/test_microchip_source.py`

**Interfaces:**
- Consumes (Task 1): `fetch_text_and_url`, `parallel_fetch_text`, `inline_images`, `build_html_document`, `sanitize_stem`, `DatasheetSource`.
- Produces:
  - `parse_oxy_toc(index_html: str) -> List[str]` (ordered GUID-*.html filenames, deduped)
  - `extract_oxy_topic(topic_html: str) -> str`
  - `oxy_doc_title(index_html: str) -> Optional[str]`
  - `oxy_root_guid(url: str) -> Optional[str]`
  - `class MicrochipOnlineDocsSource(DatasheetSource)` with `name = "microchip"`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_microchip_source.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_microchip_source.py -v`
Expected: FAIL with `ImportError: cannot import name 'MicrochipOnlineDocsSource'`

- [ ] **Step 3: Implement the Microchip adapter**

Append to `scripts/datasheet_sources.py`:

```python
_OXY_TOPIC_RE = re.compile(r"^GUID-[A-Fa-f0-9-]+\.html$")
_GUID_RE = re.compile(
    r"GUID-[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")


def parse_oxy_toc(index_html: str) -> List[str]:
    soup = BeautifulSoup(index_html, "html.parser")
    out: List[str] = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if _OXY_TOPIC_RE.match(href) and href not in seen:
            seen.add(href)
            out.append(href)
    return out


def extract_oxy_topic(topic_html: str) -> str:
    soup = BeautifulSoup(topic_html, "html.parser")
    art = soup.find("article")
    return str(art) if art else ""


def oxy_doc_title(index_html: str) -> Optional[str]:
    soup = BeautifulSoup(index_html, "html.parser")
    if soup.title and soup.title.get_text(strip=True):
        return soup.title.get_text(strip=True)
    return None


def oxy_root_guid(url: str) -> Optional[str]:
    m = _GUID_RE.search(url)
    return m.group(0) if m else None


class MicrochipOnlineDocsSource(DatasheetSource):
    name = "microchip"

    def matches(self, url: str) -> bool:
        return urlsplit(url).netloc.lower() == "onlinedocs.microchip.com"

    def fetch(self, url: str, work_dir: Path) -> Tuple[Path, str]:
        index_html, index_url = fetch_text_and_url(url)
        topics = parse_oxy_toc(index_html)
        if not topics:
            raise SystemExit(f"Error: no topics found at {url}")
        base = index_url.rsplit("/", 1)[0] + "/"
        stem = sanitize_stem(oxy_doc_title(index_html) or oxy_root_guid(index_url) or "datasheet")
        topic_urls = [urljoin(base, t) for t in topics]
        fetched = parallel_fetch_text(topic_urls)
        bodies = []
        for tu in topic_urls:
            html = fetched.get(tu)
            if not html:
                continue
            body = extract_oxy_topic(html)
            if body:
                bodies.append(inline_images(body, tu))
        if not bodies:
            raise SystemExit(f"Error: no topic content retrieved for {url}")
        out = work_dir / f"{stem}.html"
        out.write_text(build_html_document(stem, "\n".join(bodies)), encoding="utf-8")
        return out, stem
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_microchip_source.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/datasheet_sources.py tests/test_microchip_source.py
git commit -m "Add Microchip onlinedocs datasheet source"
```

---

## Task 4: Source registry

**Files:**
- Modify: `scripts/datasheet_sources.py`
- Test: `tests/test_source_registry.py`

**Interfaces:**
- Consumes: `TIDocumentViewerSource`, `MicrochipOnlineDocsSource`.
- Produces:
  - `SOURCES: list[DatasheetSource]`
  - `find_source(url: str) -> Optional[DatasheetSource]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_source_registry.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_source_registry.py -v`
Expected: FAIL with `ImportError: cannot import name 'find_source'`

- [ ] **Step 3: Implement the registry**

Append to `scripts/datasheet_sources.py`:

```python
SOURCES: List[DatasheetSource] = [
    TIDocumentViewerSource(),
    MicrochipOnlineDocsSource(),
]


def find_source(url: str) -> Optional[DatasheetSource]:
    return next((s for s in SOURCES if s.matches(url)), None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_source_registry.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/datasheet_sources.py tests/test_source_registry.py
git commit -m "Add datasheet source registry (find_source)"
```

---

## Task 5: Generalize docling conversion for `.html`

**Files:**
- Modify: `scripts/pdf2markdown.py` (`run_docling` at ~line 78; `convert` at ~line 243)
- Test: `tests/test_docling_cmd.py`

**Interfaces:**
- Produces: `build_docling_cmd(docling: str, source: Path, out_dir: Path, ocr: bool) -> list[str]`
- Changes `convert(source: Path, ...)` to accept `.pdf` or `.html`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_docling_cmd.py
from pathlib import Path
from pdf2markdown import build_docling_cmd


def test_pdf_cmd_includes_ocr_and_tables():
    cmd = build_docling_cmd("docling", Path("/tmp/a.pdf"), Path("/out"), ocr=True)
    assert "--ocr" in cmd
    assert "--tables" in cmd and "accurate" in cmd
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
    assert "--image-export-mode" in cmd and "embedded" in cmd
    assert "--output" in cmd and "/out" in cmd
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_docling_cmd.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_docling_cmd'`

- [ ] **Step 3: Refactor `run_docling` to use `build_docling_cmd`**

In `scripts/pdf2markdown.py`, replace the body of `run_docling` (the `cmd = [...]` construction through the `subprocess.run`) so the command comes from a new pure function. Add above `run_docling`:

```python
def build_docling_cmd(docling: str, source: Path, out_dir: Path, ocr: bool) -> list:
    """Build the docling CLI argv for a .pdf or .html source."""
    cmd = [docling, str(source), "--to", "md", "--image-export-mode", "embedded"]
    if source.suffix.lower() == ".pdf":
        if ocr:
            cmd.append("--ocr")
        cmd += ["--tables", "--table-mode", "accurate"]
    cmd += ["--output", str(out_dir)]
    return cmd
```

Then change `run_docling` to:

```python
def run_docling(docling: str, source: Path, out_dir: Path, ocr: bool) -> Path:
    """Run docling embedded-image conversion; return the produced .md file."""
    cmd = build_docling_cmd(docling, source, out_dir, ocr)
    print(f"Converting {source.name} with docling...", flush=True)
    subprocess.run(cmd, check=True)

    produced = out_dir / f"{source.stem}.md"
    if produced.is_file():
        return produced
    candidates = list(out_dir.glob("*.md"))
    if len(candidates) == 1:
        return candidates[0]
    raise SystemExit("Error: docling did not produce the expected .md output")
```

(Keep the existing fallback/`raise` lines after `candidates` exactly as they are now — only the signature param name `pdf`→`source` and the `cmd` construction change.)

- [ ] **Step 4: Generalize `convert()` to accept `.html`**

In `convert()` (currently `def convert(pdf: Path, ...)`), rename the parameter `pdf`→`source` and update the suffix validation. Replace:

```python
    if not pdf.is_file():
        raise SystemExit(f"Error: not a file: {pdf}")
    if pdf.suffix.lower() != ".pdf":
        raise SystemExit(f"Error: expected a .pdf file, got: {pdf}")

    stem = pdf.stem
```
with:
```python
    if not source.is_file():
        raise SystemExit(f"Error: not a file: {source}")
    if source.suffix.lower() not in (".pdf", ".html"):
        raise SystemExit(f"Error: expected a .pdf or .html file, got: {source}")

    stem = source.stem
    suffix = source.suffix.lower()
```

Then update the copy + docling call inside `convert()`. Replace the `pdf_copy = output_dir / f"{stem}.pdf"` line with `source_copy = output_dir / f"{stem}{suffix}"`, replace the copy guard:
```python
    if pdf.resolve() != pdf_copy.resolve():
        shutil.copy2(pdf, pdf_copy)
```
with:
```python
    if source.resolve() != source_copy.resolve():
        shutil.copy2(source, source_copy)
```
and update the docling call `embedded_md = run_docling(docling, pdf, tmp_dir, ocr)` → `run_docling(docling, source, tmp_dir, ocr)`. Finally change the return `return pdf_copy, out_md, count, stats` → `return source_copy, out_md, count, stats`. (All other lines in `convert()` already key off `stem`/`out_md`/`images_dir` and are unchanged.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_docling_cmd.py tests/test_duplicate_images.py -v`
Expected: PASS (the new command tests + the existing dedupe tests that call `convert`/`split_images` still pass)

- [ ] **Step 6: Commit**

```bash
git add scripts/pdf2markdown.py tests/test_docling_cmd.py
git commit -m "Generalize docling conversion to accept .html sources"
```

---

## Task 6: 3-way URL dispatch in `main()`

**Files:**
- Modify: `scripts/pdf2markdown.py` (`main()` URL branch, currently ~lines 311-327 post-`f6b47cf`)
- Test: `tests/test_url_dispatch.py`

**Interfaces:**
- Consumes: `find_source` (from `datasheet_sources`), existing `is_url`, `download_pdf`, `convert`.
- Produces: `_looks_like_pdf(url: str) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_url_dispatch.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_url_dispatch.py -v`
Expected: FAIL with `AttributeError: module 'pdf2markdown' has no attribute '_looks_like_pdf'`

- [ ] **Step 3: Implement dispatch**

In `scripts/pdf2markdown.py`, add the import near the top (after the stdlib imports):

```python
from datasheet_sources import find_source
```

Add the helper (near `is_url`):

```python
def _looks_like_pdf(url: str) -> bool:
    return Path(urlsplit(url).path).suffix.lower() == ".pdf"
```

Replace the current URL branch in `main()` — the block that begins `download_dir = None` and ends at the `finally:` clean-up — with:

```python
    download_dir = None
    if is_url(args.pdf):
        download_dir = Path(tempfile.mkdtemp(prefix="pdf2markdown_dl_"))
        source = find_source(args.pdf)
        if source is not None:
            print(f"Fetching {source.name} HTML datasheet ...", flush=True)
            input_path, _ = source.fetch(args.pdf, download_dir)
        elif _looks_like_pdf(args.pdf):
            input_path = download_pdf(args.pdf, download_dir)
        else:
            raise SystemExit(
                f"Error: unsupported URL (no datasheet source matched, and not a "
                f".pdf): {args.pdf}")
        default_output = Path.cwd()
    else:
        input_path = Path(args.pdf).resolve()
        default_output = input_path.parent

    try:
        output_dir = args.output_dir if args.output_dir is not None else default_output
        source_copy, out_md, count, stats = convert(
            input_path, output_dir, ocr=args.ocr, force=args.force,
            postprocess=args.postprocess,
        )
    finally:
        if download_dir is not None:
            shutil.rmtree(download_dir, ignore_errors=True)
```

Update the summary print that referenced `pdf_copy` to use `source_copy`: change `print(f"  PDF copy : {pdf_copy}")` to `print(f"  Source   : {source_copy}")`. Ensure `urlsplit` is imported (it already is, from the `f6b47cf` URL feature).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_url_dispatch.py tests/test_url_input.py -v`
Expected: PASS (new dispatch tests + the existing URL tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/pdf2markdown.py tests/test_url_dispatch.py
git commit -m "Route datasheet URLs through vendor sources in main()"
```

---

## Task 7: Docs + full regression run

**Files:**
- Modify: `CLAUDE.md`
- Modify: `scripts/pdf2markdown` (usage comment)

- [ ] **Step 1: Update CLAUDE.md**

In the "Running" section of `CLAUDE.md`, after the existing URL bullet, add:

```markdown
- A first argument that is a **vendor HTML-datasheet URL** is fetched and
  consolidated into one self-contained HTML, then converted by the same docling
  pipeline. Supported vendors: **TI** (`www.ti.com/document-viewer/<part>/datasheet`)
  and **Microchip** (`onlinedocs.microchip.com/g/GUID-…` or `/oxy/…`). New vendors
  are added by implementing `DatasheetSource` in `scripts/datasheet_sources.py`
  and registering it in `SOURCES`.
```

Also add a short "Architecture" entry for `scripts/datasheet_sources.py` describing the `DatasheetSource` interface, the TI/Microchip adapters, and the convergence-after-docling design.

- [ ] **Step 2: Update the wrapper usage comment**

In `scripts/pdf2markdown`, extend the usage comment's first-argument description to mention vendor HTML-datasheet URLs (TI document-viewer, Microchip onlinedocs) alongside PDF path / PDF URL.

- [ ] **Step 3: Run the full unit test suite**

Run: `pytest -q` (network/`slow` tests excluded by default — see Task 8)
Expected: PASS (all helper, TI, Microchip, registry, docling-cmd, dispatch, and pre-existing image tests)

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md scripts/pdf2markdown
git commit -m "Document HTML datasheet support"
```

---

## Task 8: Opt-in network integration test

**Files:**
- Create: `tests/test_datasheet_network.py`

- [ ] **Step 1: Write the opt-in integration test**

```python
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
```

- [ ] **Step 2: Run it (requires network; not part of default suite)**

Run: `pytest -m slow tests/test_datasheet_network.py -v`
Expected: PASS (both fetch real documents). If it fails due to a vendor site change, that is a real signal — investigate before proceeding.

- [ ] **Step 3: Commit**

```bash
git add tests/test_datasheet_network.py
git commit -m "Add opt-in network integration tests for datasheet sources"
```

---

## Verification (end-to-end, incl. the PDF-vs-HTML comparison)

This requires provisioning the docling venv once (PyTorch + OCR models — slow, network-heavy, first run only). Run the wrapper, which provisions automatically:

1. **Regression — local PDF and PDF URL still work:**
   ```
   scripts/pdf2markdown --no-postprocess 'https://www.ti.com/lit/ds/symlink/ucc256404.pdf?ts=1' /tmp/ti_pdf
   ```
   Expect `/tmp/ti_pdf/ucc256404.{pdf,md}` + `ucc256404.images/`.

2. **TI HTML route:**
   ```
   scripts/pdf2markdown 'https://www.ti.com/document-viewer/ucc256404/datasheet' /tmp/ti_html
   ```
   Expect `/tmp/ti_html/ucc256404.{html,md}` + images. Confirm Markdown contains real `|`-delimited tables (not OCR text) and that images flowed through (the `.images/` dir is populated, i.e. docling re-embedded the inlined data-URIs and `split_images` decoded them — **this is the key integration risk to confirm**; if images are dropped, fall back to writing image files + referenced links instead of data-URI inlining).

3. **Microchip both routes:**
   ```
   scripts/pdf2markdown 'https://ww1.microchip.com/downloads/aemDocuments/documents/MCU08/ProductDocuments/DataSheets/AVR128DA28-32-48-64-Data-Sheet-DS40002183.pdf' /tmp/mc_pdf
   scripts/pdf2markdown 'https://onlinedocs.microchip.com/g/GUID-3EE676DF-490E-41BC-98F0-5774B35DC989' /tmp/mc_html
   ```

4. **Compare** each PDF-route `.md` against its HTML-route `.md` on: table fidelity (clean Markdown vs OCR), heading/list structure, equations, figure count/quality, overall fidelity. Write up which route wins where (the original motivation for this work).

---

## Self-Review (completed during planning)

- **Spec coverage:** interface (T1,4) ✓; TI adapter (T2) ✓; Microchip adapter (T3) ✓; conversion reuse / docling `.html` (T5) ✓; `main()` dispatch (T6) ✓; deps + wrapper probe (T1) ✓; naming/stem rules (T2,T3) ✓; error handling idioms (all tasks) ✓; unit + opt-in network tests (T1-4,8) ✓; PDF-vs-HTML comparison (Verification) ✓.
- **Placeholder scan:** no TBD/TODO; every code step shows complete code.
- **Type consistency:** `fetch(url, work_dir) -> (Path, str)`, `find_source -> Optional[DatasheetSource]`, `build_docling_cmd -> list`, `convert(source, ...)` consistent across tasks; helper signatures in Task 1 match their call sites in Tasks 2-3, 6.
- **Known integration risk flagged:** docling's handling of `<img src="data:...">` in HTML input (Verification step 2) — with a documented fallback (write image files + referenced links) if embedded data-URIs don't survive.
