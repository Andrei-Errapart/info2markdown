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
from html import escape as _escape
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


_SSL_CTX = None


def _ssl_context() -> ssl.SSLContext:
    global _SSL_CTX
    if _SSL_CTX is None:
        _SSL_CTX = (ssl.create_default_context(cafile=certifi.where())
                    if _CERTIFI else ssl.create_default_context())
    return _SSL_CTX


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
            try:
                results[futs[fut]] = fut.result()
            except Exception:
                results[futs[fut]] = None
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
            f"<title>{_escape(title)}</title></head><body>{body_html}</body></html>")


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
                print(f"Warning: failed to fetch {su}", file=sys.stderr)
                continue
            frag = extract_ti_fragment(html)
            if not frag:
                print(f"Warning: no subsection content in {su}", file=sys.stderr)
                continue
            bodies.append(inline_images(frag, "https://www.ti.com/"))
        if not bodies:
            raise SystemExit(f"Error: no section content retrieved for {url}")
        out = work_dir / f"{part}.html"
        out.write_text(build_html_document(part, "\n".join(bodies)), encoding="utf-8")
        return out, part
