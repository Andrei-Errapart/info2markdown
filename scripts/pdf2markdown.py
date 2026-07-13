#!/usr/bin/env python3
"""
pdf2markdown.py - Convert a PDF to clean Markdown with external image files.

This is the conversion engine; it expects docling to be importable/runnable in
the current environment. Normally you invoke the `pdf2markdown` shell wrapper,
which provisions a local virtual environment and runs this script inside it.

Given an input PDF and an output directory, this produces in that directory:
  - a copy of the original PDF                       (<stem>.pdf)
  - the extracted images in a subdirectory           (<stem>.images/)
  - a Markdown file referring to those image files   (<stem>.md)
  - an image occurrence source map                   (<stem>.image-map.json)

docling (https://github.com/DS4SD/docling) first emits Markdown with base64-
embedded PNG images; this script then splits those out into separate image
files, canonicalizes exact duplicates, and rewrites the links to point at them.

A post-processing pass (see image_postprocess.py) then inspects every extracted
image and, where confident, replaces it with something better: tables and
text-only images are flattened into inline Markdown, line-art diagrams are
traced to SVG, and photos are left as-is. Use --no-postprocess to skip it.

Usage:
    pdf2markdown.py [options] <input.pdf> [output_dir]

Options:
    -f, --force          Overwrite existing <stem>.md / <stem>.images/ / image map
        --no-ocr         Skip OCR during docling conversion (faster; text-only PDFs)
        --no-postprocess Skip the image post-processing pass (keep all images as PNG)
    -h, --help           Show this help
"""

import argparse
import base64
import hashlib
import json
import re
import shutil
import ssl
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import unquote, urlsplit

from datasheet_sources import find_source
from unimernet_formula import build_pdf_converter

try:
    import certifi
    _certifi = True
except ImportError:
    _certifi = False

# Matches docling's embedded images: ![alt](data:image/<fmt>;base64,<data>)
# (ported from /home/andrei/2026/mdoc/divide_png_md)
IMAGE_RE = re.compile(
    r'!\[([^\]]*)\]\(data:image/(png|jpeg|jpg);base64,([A-Za-z0-9+/=]+)\)'
)

# Matches a Markdown image: ![alt](target)
IMG_REF_RE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')


def _sanitize_image_alt_text(value: object) -> Optional[str]:
    """Return compact, human-readable alt text or None for docling dumps."""
    if value is None:
        return None
    if callable(value):
        try:
            value = value()
        except TypeError:
            return None
    if isinstance(value, (list, tuple)):
        value = " ".join(str(part) for part in value if part is not None)
    value = str(value).strip()
    if not value:
        return None
    if value.startswith("<bound method") or "data:image" in value:
        return None
    if len(value) > 300:
        return None
    return re.sub(r"\s+", " ", value)


def _attr_or_key(obj: object, name: str, default: object = None) -> object:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _first_attr_or_key(obj: object, names: Tuple[str, ...]) -> object:
    for name in names:
        value = _attr_or_key(obj, name)
        if value is not None:
            return value
    return None


def _bbox_to_map(bbox: object) -> Optional[Dict[str, float]]:
    if bbox is None:
        return None
    values = {
        "l": _first_attr_or_key(bbox, ("l", "left", "x0")),
        "t": _first_attr_or_key(bbox, ("t", "top", "y0")),
        "r": _first_attr_or_key(bbox, ("r", "right", "x1")),
        "b": _first_attr_or_key(bbox, ("b", "bottom", "y1")),
    }
    if any(value is None for value in values.values()):
        return None
    try:
        return {key: float(value) for key, value in values.items()}
    except (TypeError, ValueError):
        return None


def _page_from_prov(prov: object) -> Optional[int]:
    value = _first_attr_or_key(prov, ("page_no", "page", "page_number"))
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _item_alt_text(item: object) -> Optional[str]:
    value = _first_attr_or_key(
        item,
        ("alt", "alt_text", "text", "caption_text", "name", "label"),
    )
    return _sanitize_image_alt_text(value)


def _iter_docling_items(document: object) -> list:
    if document is None:
        return []
    iterator = getattr(document, "iterate_items", None)
    if callable(iterator):
        try:
            return [item[0] if isinstance(item, tuple) else item for item in iterator()]
        except Exception:
            pass

    items = []
    for name in ("pictures", "images", "figures", "body"):
        value = _attr_or_key(document, name)
        if isinstance(value, (list, tuple)):
            items.extend(value)
    return items


def extract_docling_image_metadata(document: object) -> list[dict]:
    """Best-effort ordered image metadata from a docling document."""
    entries = []
    for item in _iter_docling_items(document):
        cls_name = item.__class__.__name__.lower()
        label = str(_attr_or_key(item, "label", "")).lower()
        has_image = _attr_or_key(item, "image") is not None
        if not has_image and "picture" not in cls_name and "image" not in cls_name and "picture" not in label:
            continue

        provs = _attr_or_key(item, "prov")
        if provs is None:
            prov = None
        elif isinstance(provs, (list, tuple)):
            prov = provs[0] if provs else None
        else:
            prov = provs

        bbox = _bbox_to_map(_attr_or_key(prov, "bbox"))
        entries.append({
            "alt": _item_alt_text(item),
            "page": _page_from_prov(prov),
            "bbox": bbox,
            "coord_system": "docling" if bbox is not None else None,
        })
    return entries


def run_docling_html(source: Path, out_dir: Path) -> Tuple[Path, list[dict]]:
    """Convert an HTML source via docling's Python API with image fetching on.

    The docling CLI does not expose the HTML backend's ``fetch_images`` option,
    so inlined ``<img>`` data-URIs are dropped and replaced by "Image not
    available" placeholders. Calling the API with ``fetch_images=True`` makes
    docling decode the data-URIs and re-embed them as base64 in the Markdown,
    which ``split_images()`` then extracts — identical to the PDF route.

    A custom serializer also emits ``<sub>``/``<sup>`` for the subscript /
    superscript formatting docling's HTML backend captures from ``<sub>``/
    ``<sup>`` tags — the default Markdown serializer drops it, flattening prose
    like ``V_comp`` to ``V comp``.
    """
    from docling.document_converter import DocumentConverter, HTMLFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.backend_options import HTMLBackendOptions
    from docling_core.types.doc import ImageRefMode
    from docling_core.transforms.serializer.markdown import (
        MarkdownDocSerializer, MarkdownParams)

    class _SubSupSerializer(MarkdownDocSerializer):
        def serialize_subscript(self, text: str, **kwargs) -> str:
            return f"<sub>{text}</sub>"

        def serialize_superscript(self, text: str, **kwargs) -> str:
            return f"<sup>{text}</sup>"

    print(f"Converting {source.name} with docling (HTML, images enabled)...",
          flush=True)
    converter = DocumentConverter(format_options={
        InputFormat.HTML: HTMLFormatOption(
            backend_options=HTMLBackendOptions(fetch_images=True)
        )
    })
    result = converter.convert(str(source))
    md_text = _SubSupSerializer(
        doc=result.document,
        params=MarkdownParams(image_mode=ImageRefMode.EMBEDDED),
    ).serialize().text
    # docling leaves a space between a variable and its subscript run
    # ("R <sub>FB</sub>"); tighten it to "R<sub>FB</sub>".
    md_text = re.sub(r"(\w)[ \t]+(<su[bp]>)", r"\1\2", md_text)
    out_md = out_dir / f"{source.stem}.md"
    out_md.write_text(md_text, encoding="utf-8")
    return out_md, extract_docling_image_metadata(result.document)


def run_docling_pdf(source: Path, out_dir: Path, ocr: bool) -> Tuple[Path, list[dict]]:
    """Convert a PDF via docling's Python API, OCRing formulas with UniMERNet-base.

    Mirrors the HTML route: convert, then serialise to embedded-image Markdown
    (base64 data-URIs) so split_images() extracts the images exactly as before.
    """
    from docling_core.types.doc import ImageRefMode
    print(f"Converting {source.name} with docling (UniMERNet formulas)...", flush=True)
    converter = build_pdf_converter(ocr)
    result = converter.convert(str(source))
    md_text = result.document.export_to_markdown(image_mode=ImageRefMode.EMBEDDED)
    out_md = out_dir / f"{source.stem}.md"
    out_md.write_text(md_text, encoding="utf-8")
    return out_md, extract_docling_image_metadata(result.document)


def run_docling(source: Path, out_dir: Path, ocr: bool) -> Tuple[Path, list[dict]]:
    """Dispatch to the HTML or PDF docling Python-API route."""
    if source.suffix.lower() == ".html":
        return run_docling_html(source, out_dir)
    return run_docling_pdf(source, out_dir, ocr)


def _empty_image_map_entry(
    occurrence: int,
    alt: str,
    original_file: str,
    data: bytes,
    docling_metadata: Optional[dict],
) -> dict:
    metadata = docling_metadata or {}
    clean_alt = _sanitize_image_alt_text(metadata.get("alt")) or _sanitize_image_alt_text(alt) or "Image"
    return {
        "occurrence": occurrence,
        "alt": clean_alt,
        "original_file": original_file,
        "final_file": original_file,
        "status": "kept",
        "page": metadata.get("page"),
        "bbox": metadata.get("bbox"),
        "coord_system": metadata.get("coord_system"),
        "content_sha256": hashlib.sha256(data).hexdigest(),
    }


def split_images(
    source_md: Path,
    out_md: Path,
    images_dir: Path,
    image_entries: Optional[list[dict]] = None,
    docling_metadata: Optional[list[dict]] = None,
) -> int:
    """Extract embedded images from source_md into images_dir, write rewritten out_md.

    Ported from /home/andrei/2026/mdoc/divide_png_md. Returns the image count.
    """
    content = source_md.read_text(encoding="utf-8")
    counter = {"n": 0, "errors": 0}
    state = {"dir_created": False}

    def ensure_dir() -> None:
        if not state["dir_created"]:
            images_dir.mkdir(parents=True, exist_ok=True)
            state["dir_created"] = True

    def replace(match: "re.Match[str]") -> str:
        alt, fmt, b64 = match.group(1), match.group(2), match.group(3)
        clean_alt = _sanitize_image_alt_text(alt) or "Image"
        try:
            data = base64.b64decode(b64)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"Warning: failed to decode an image: {exc}", file=sys.stderr)
            counter["errors"] += 1
            return match.group(0)

        counter["n"] += 1
        ext = "jpg" if fmt == "jpeg" else fmt
        digest = hashlib.md5(data).hexdigest()[:8]
        fname = f"image_{counter['n']:03d}_{digest}.{ext}"
        ensure_dir()
        (images_dir / fname).write_bytes(data)
        if image_entries is not None:
            metadata = None
            if docling_metadata and counter["n"] <= len(docling_metadata):
                metadata = docling_metadata[counter["n"] - 1]
            image_entries.append(
                _empty_image_map_entry(counter["n"], clean_alt, fname, data, metadata)
            )
        return f"![{clean_alt}]({images_dir.name}/{fname})"

    new_content = IMAGE_RE.sub(replace, content)
    out_md.write_text(new_content, encoding="utf-8")

    if counter["errors"]:
        print(
            f"  ({counter['errors']} image(s) could not be decoded and were left embedded)",
            file=sys.stderr,
        )
    return counter["n"]


def image_content_hash(image_path: Path) -> str:
    """Return the content hash used to canonicalize extracted image files."""
    h = hashlib.sha256()
    with image_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def deduplicate_images(md_path: Path, images_dirname: str) -> Dict[str, object]:
    """Canonicalize exact duplicate extracted images and rewrite Markdown refs.

    Exact-byte duplicates are collapsed to one SHA-256-named file. The helper
    boundary is intentionally content-hash based so perceptual dedupe can be
    layered in later without changing callers.
    """
    content = md_path.read_text(encoding="utf-8")
    images_dir = md_path.parent / images_dirname
    prefix = f"{images_dirname}/"
    stats: Dict[str, object] = {
        "hash_algorithm": "sha256",
        "total_refs": 0,
        "in_scope_refs": 0,
        "unique_images": 0,
        "duplicate_refs": 0,
        "renamed_files": 0,
        "removed_files": 0,
        "missing_files": 0,
        "canonical_by_original": {},
        "duplicate_groups": {},
    }

    canonical_by_digest: Dict[str, str] = {}
    canonical_by_original: Dict[str, str] = {}
    duplicate_groups: Dict[str, list] = {}

    def resolve_target(target: str) -> Optional[Tuple[str, Path]]:
        stripped = target.strip()
        if not stripped.startswith(prefix):
            return None
        rel_name = stripped[len(prefix):]
        rel_path = Path(rel_name)
        if not rel_name or len(rel_path.parts) != 1:
            return None
        return rel_name, images_dir / rel_name

    def replace(match: "re.Match[str]") -> str:
        alt, target = match.group(1), match.group(2)
        stats["total_refs"] = int(stats["total_refs"]) + 1
        resolved = resolve_target(target)
        if resolved is None:
            return match.group(0)

        rel_name, image_path = resolved
        stats["in_scope_refs"] = int(stats["in_scope_refs"]) + 1
        if not image_path.is_file():
            stats["missing_files"] = int(stats["missing_files"]) + 1
            return match.group(0)

        digest = image_content_hash(image_path)
        suffix = image_path.suffix.lower() or ".img"
        seen_digest = digest in canonical_by_digest
        canonical_name = canonical_by_digest.setdefault(digest, f"{digest}{suffix}")
        if seen_digest:
            stats["duplicate_refs"] = int(stats["duplicate_refs"]) + 1

        canonical_by_original[rel_name] = canonical_name
        duplicate_groups.setdefault(canonical_name, []).append(rel_name)
        return f"![{alt}]({prefix}{canonical_name})"

    new_content = IMG_REF_RE.sub(replace, content)

    stats["unique_images"] = len(canonical_by_digest)
    stats["canonical_by_original"] = canonical_by_original
    stats["duplicate_groups"] = {
        canonical: originals
        for canonical, originals in duplicate_groups.items()
        if len(originals) > 1
    }

    if images_dir.is_dir():
        canonical_sources: Dict[str, Path] = {}
        for original_name, canonical_name in canonical_by_original.items():
            canonical_sources.setdefault(canonical_name, images_dir / original_name)

        for canonical_name, source_path in canonical_sources.items():
            canonical_path = images_dir / canonical_name
            if source_path == canonical_path:
                continue
            if not canonical_path.exists():
                source_path.rename(canonical_path)
                stats["renamed_files"] = int(stats["renamed_files"]) + 1

        referenced = set(canonical_sources)
        touched = set(canonical_by_original)
        for f in list(images_dir.iterdir()):
            if f.is_file() and f.name in touched and f.name not in referenced:
                f.unlink()
                stats["removed_files"] = int(stats["removed_files"]) + 1
        if images_dir.exists() and not any(images_dir.iterdir()):
            images_dir.rmdir()

    md_path.write_text(new_content, encoding="utf-8")
    return stats


def write_image_map(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def update_image_map_for_dedupe(entries: list[dict], dedupe_stats: Dict[str, object]) -> None:
    canonical_by_original = dedupe_stats.get("canonical_by_original", {})
    duplicate_groups = dedupe_stats.get("duplicate_groups", {})
    duplicate_originals = set()
    if isinstance(duplicate_groups, dict):
        for originals in duplicate_groups.values():
            if isinstance(originals, list):
                duplicate_originals.update(originals[1:])

    if not isinstance(canonical_by_original, dict):
        return

    for entry in entries:
        original = entry.get("original_file")
        canonical = canonical_by_original.get(original)
        if canonical is None:
            entry["final_file"] = original
            entry["status"] = "missing"
            continue
        entry["final_file"] = canonical
        entry["status"] = "deduped" if original in duplicate_originals else "kept"


def _in_scope_markdown_targets(md_text: str, images_dirname: str) -> list[str]:
    prefix = f"{images_dirname}/"
    return [
        match.group(2).strip()[len(prefix):]
        for match in IMG_REF_RE.finditer(md_text)
        if match.group(2).strip().startswith(prefix)
    ]


def _target_matches_entry(target: str, entry: dict) -> bool:
    final_file = entry.get("final_file")
    if not final_file:
        return False
    if target == final_file:
        return True
    return target == Path(str(final_file)).with_suffix(".svg").name


def update_image_map_from_markdown(
    entries: list[dict],
    md_path: Path,
    images_dirname: str,
) -> None:
    md_text = md_path.read_text(encoding="utf-8") if md_path.is_file() else ""
    targets = _in_scope_markdown_targets(md_text, images_dirname)
    images_dir = md_path.parent / images_dirname

    target_index = 0
    same_ref_count = len(targets) == len(entries)
    for entry_index, entry in enumerate(entries):
        target = None
        if target_index < len(targets):
            candidate = targets[target_index]
            remaining_targets = len(targets) - target_index
            remaining_entries = len(entries) - entry_index
            if same_ref_count or _target_matches_entry(candidate, entry):
                target = candidate
                target_index += 1
            elif remaining_targets >= remaining_entries:
                target = candidate
                target_index += 1

        if target is None:
            entry["final_file"] = None
            entry["status"] = "inlined"
            continue

        previous_status = entry.get("status")
        original_suffix = Path(str(entry.get("original_file", ""))).suffix.lower()
        target_suffix = Path(target).suffix.lower()
        entry["final_file"] = target
        if not (images_dir / target).is_file():
            entry["status"] = "missing"
        elif target_suffix == ".svg" and original_suffix != ".svg":
            entry["status"] = "vectorized"
        elif previous_status == "deduped":
            entry["status"] = "deduped"
        else:
            entry["status"] = "kept"


_PAGE_HEADER_HEADING_RE = re.compile(
    r"(?mi)^[ \t]{0,3}#{1,6}[ \t]+(?:AND\d{5,}/D|CONFIDENTIAL AND PROPRIETARY[^\n]*)[ \t]*\n?"
)
# A ``Table N.`` caption docling promoted to a ``#`` heading (bold standalone
# captions above ruled tables get mis-detected as headings). Demote to plain caption
# text -- most captions in the same document stay plain, so the heading form is the
# artifact. Only the ``#`` markers are dropped; the caption text is kept. The digit
# guard keeps real headings like ``## Table of Contents`` untouched.
_CAPTION_HEADING_RE = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+(Table[ \t]+\d+\.[^\n]*)$")
_TRAILING_PAGE_NUMBER_RE = re.compile(r"\n{2,}\d{1,4}[ \t]*\Z")
_FENCED_CODE_RE = re.compile(r"(```.*?```|~~~.*?~~~)", re.S)
_BARE_NUMBER_LINE_RE = re.compile(r"^[ \t]*[0-9][0-9|.,:; \t-]*[ \t]*$")
_PDF_SYMBOL_ESCAPES = {
    "/C0069": "\u00a9",
    "/C0109": "\u00b5",
    "/C0043": "=",
    "/C0042": "-",
    "/C0324": "/",
    "/C0087": "\u03a9",
}
_PUA_GLYPHS = {
    "\uf0a3": "\u2264",
    "\uf0b3": "\u2265",
    "\uf0b1": "\u00b1",
    "\uf0b0": "\u00b0",
    "\uf0d6": "\u00d7",
    "\uf0fc": "\u2713",
    "\uf06c": "\u2022",
    "\uf0a8": "\u2022",
    "\uf0b7": "\u2022",
}
_TEXT_ARTIFACT_REPLACEMENTS = {
    "Patent -Marking": "Patent-Marking",
    "Patent - Marking": "Patent-Marking",
    "technical - documentation": "technical-documentation",
    "technical -documentation": "technical-documentation",
    "as -is": "as-is",
    "read - only": "read-only",
    "write - only": "write-only",
    "read - write": "read-write",
    "ultra-lowpower": "ultra-low power",
    "crossconduction": "cross conduction",
    "Power-dowm": "Power-down",
    "fourlayer": "four-layer",
    "12-bitreadout": "12-bit readout",
    "OUTPUT_EN - ABLE_N": "OUTPUT_ENABLE_N",
}


def _outside_fenced_code(md: str, transform) -> str:
    parts = _FENCED_CODE_RE.split(md)
    for idx in range(0, len(parts), 2):
        parts[idx] = transform(parts[idx])
    return "".join(parts)


def _drop_bare_number_runs(text: str) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    run: list[str] = []

    def flush_run() -> None:
        nonlocal run
        if len(run) < 6:
            out.extend(run)
        run = []

    for line in lines:
        stripped = line.strip()
        if stripped and _BARE_NUMBER_LINE_RE.match(line) and not stripped.startswith("|"):
            run.append(line)
        else:
            flush_run()
            out.append(line)
    flush_run()
    return "".join(out)


def clean_markdown_text(md: str) -> str:
    """Clean recurring converter artifacts found in ground-truth error lists."""
    def transform(text: str) -> str:
        for bad, good in _PDF_SYMBOL_ESCAPES.items():
            text = text.replace(bad, good)
        for bad, good in _PUA_GLYPHS.items():
            text = text.replace(bad, good)
        text = text.replace("\u00c2", "")
        for bad, good in _TEXT_ARTIFACT_REPLACEMENTS.items():
            text = text.replace(bad, good)

        # Remove duplicated bullet glyphs while preserving Markdown list shape.
        text = re.sub(r"(?m)^([ \t]*[-*+][ \t]+)\u2022[ \t]+", r"\1", text)
        text = re.sub(r"(?m)^([ \t]*)\u2022[ \t]+", r"\1- ", text)

        text = _PAGE_HEADER_HEADING_RE.sub("", text)
        text = _CAPTION_HEADING_RE.sub(r"\1", text)
        text = _TRAILING_PAGE_NUMBER_RE.sub("\n", text)
        text = _drop_bare_number_runs(text)
        return text

    return _outside_fenced_code(md, transform)


def _image_size(path: Path) -> Optional[Tuple[int, int]]:
    try:
        from PIL import Image
        with Image.open(path) as img:
            return img.size
    except Exception:
        return None


def remove_repeated_page_furniture_images(md_path: Path, images_dirname: str) -> Dict[str, int]:
    """Drop repeated low banner/logo images that are likely page furniture."""
    content = md_path.read_text(encoding="utf-8")
    images_dir = md_path.parent / images_dirname
    prefix = f"{images_dirname}/"
    targets = _in_scope_markdown_targets(content, images_dirname)
    ref_counts: Dict[str, int] = {}
    for target in targets:
        ref_counts[target] = ref_counts.get(target, 0) + 1

    drop: set[str] = set()
    if images_dir.is_dir():
        for target, count in ref_counts.items():
            if count < 2:
                continue
            size = _image_size(images_dir / target)
            if size is None:
                continue
            width, height = size
            if height <= 40 and width >= 80 and width / max(height, 1) >= 3:
                drop.add(target)

    if not drop:
        return {"removed_refs": 0, "removed_files": 0}

    removed_refs = 0

    def replace(match: "re.Match[str]") -> str:
        nonlocal removed_refs
        target = match.group(2).strip()
        if target.startswith(prefix) and target[len(prefix):] in drop:
            removed_refs += 1
            return ""
        return match.group(0)

    new_content = IMG_REF_RE.sub(replace, content)
    md_path.write_text(new_content, encoding="utf-8")

    removed_files = 0
    if images_dir.is_dir():
        remaining = set(_in_scope_markdown_targets(new_content, images_dirname))
        for target in drop:
            if target not in remaining:
                path = images_dir / target
                if path.is_file():
                    path.unlink()
                    removed_files += 1
        if images_dir.exists() and not any(images_dir.iterdir()):
            images_dir.rmdir()

    return {"removed_refs": removed_refs, "removed_files": removed_files}


def is_url(value: str) -> bool:
    """True if the argument is an http(s) URL rather than a local path."""
    return urlsplit(value).scheme in ("http", "https")


def _url_stem(url: str) -> str:
    """Derive a clean filename stem from a URL's path (query string ignored)."""
    name = Path(unquote(urlsplit(url).path)).name
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).stem).strip("._")
    return stem or "document"


def download_pdf(url: str, dest_dir: Path) -> Path:
    """Download a PDF from `url` into `dest_dir`, returning the local path.

    Names the file `<url-stem>.pdf` so the rest of the pipeline (which keys off
    pdf.stem) behaves exactly as for a local file. Sets a browser-like
    User-Agent (many vendor sites 403 the default urllib agent) and validates
    the payload really is a PDF.
    """
    dest = dest_dir / f"{_url_stem(url)}.pdf"
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; pdf2markdown)"}
    )
    ctx = (ssl.create_default_context(cafile=certifi.where()) if _certifi
           else ssl.create_default_context())
    print(f"Downloading {url} ...", flush=True)
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp, \
                open(dest, "wb") as fh:
            shutil.copyfileobj(resp, fh)
    except urllib.error.URLError as exc:
        raise SystemExit(f"Error: failed to download {url}: {exc.reason}")
    # A redirect to an HTML error/login page is the common failure mode.
    with open(dest, "rb") as fh:
        if not fh.read(5).startswith(b"%PDF"):
            raise SystemExit(f"Error: downloaded content from {url} is not a PDF")
    return dest


_LATEX_TOKEN_RE = re.compile(r"\\[A-Za-z]+|\\.|\s+|[^\\\s]")
_LATEX_WORD_RE = re.compile(r"\\[A-Za-z]+")


def _normalize_latex(latex: str) -> str:
    """Collapse the per-token spaces docling's formula models emit
    (``R _ { B L k } = 1 5`` -> ``R_{BLk}=15``) while keeping the space that
    terminates a ``\\word`` command before a letter, so ``\\mu A``,
    ``\\max off`` and ``\\,`` survive intact."""
    toks = _LATEX_TOKEN_RE.findall(latex)
    out: list = []
    for j, tok in enumerate(toks):
        if tok.isspace():
            prev = out[-1] if out else ""
            nxt = toks[j + 1] if j + 1 < len(toks) else ""
            if _LATEX_WORD_RE.fullmatch(prev) and nxt[:1].isalpha():
                out.append(" ")
        else:
            out.append(tok)
    return "".join(out)


# UniMERNet wraps most variables/units in ``\mathsf`` (and some in ``\mathrm``),
# inconsistently (``L_{\mathsf{M}}`` beside ``L_{\mathrm{R}}``). Unwrap both to
# plain italic for one consistent, clean style (the pre-UniMERNet output used
# neither). Tune which commands are stripped here.
_FONT_STRIP_CMDS = (r"\mathsf", r"\mathrm")
# A thin space UniMERNet sometimes emits *inside* a number (``1\,2`` should be
# ``12``); a real number->unit thin space (``84.4\,\mu H``) is followed by a
# letter/command, not a digit, so it is kept.
_THIN_SPACE_IN_NUMBER_RE = re.compile(r"(?<=\d)\\,(?=\d)")
_WORD_TAIL_RE = re.compile(r"\\[A-Za-z]+$")


def _strip_font_commands(latex: str) -> str:
    r"""Unwrap ``\mathsf{...}`` / ``\mathrm{...}`` to their content.

    Balanced-brace aware (content nests: ``\mathsf{V_{TH}}`` -> ``V_{TH}``). A
    space is inserted when a ``\word`` command would otherwise run into the
    content's first letter (``\times\mathsf{N}`` -> ``\times N``, not
    ``\timesN``)."""
    for cmd in _FONT_STRIP_CMDS:
        needle = cmd + "{"
        while True:
            i = latex.find(needle)
            if i == -1:
                break
            depth, k = 1, i + len(needle)
            while k < len(latex) and depth:
                depth += (latex[k] == "{") - (latex[k] == "}")
                k += 1
            if depth != 0:
                break  # unbalanced braces: leave as-is
            content = latex[i + len(needle):k - 1]
            sep = " " if (content[:1].isalpha()
                          and _WORD_TAIL_RE.search(latex[:i])) else ""
            latex = latex[:i] + sep + content + latex[k:]
    return latex


def _normalize_math(md: str) -> str:
    """Normalise the LaTeX inside every ``$$...$$`` and ``$...$`` span (only
    spans that look like math, to leave a stray ``$`` in prose alone)."""
    def norm(inner: str) -> str:
        if not re.search(r"[\\_^{}]", inner):
            return inner
        inner = _normalize_latex(inner)
        inner = _strip_font_commands(inner)
        return _THIN_SPACE_IN_NUMBER_RE.sub("", inner)
    md = re.sub(r"\$\$(.+?)\$\$", lambda m: f"$${norm(m.group(1))}$$", md, flags=re.S)
    md = re.sub(r"(?<!\$)\$(?!\$)([^$\n]+?)\$(?!\$)",
                lambda m: f"${norm(m.group(1))}$", md)
    return md


# Subscripts shorter than this (single char: L_N, C_R) would false-match 2-letter
# words/acronyms in prose, so they are not standardised. Tune here.
SUBSCRIPT_MIN_SUB_LEN = 2
# A $$...$$ / $...$ span, captured so re.split keeps it.
_MATH_SPAN_RE = re.compile(r"(\$\$.+?\$\$|(?<!\$)\$(?!\$)[^$\n]+?\$(?!\$))", re.S)
# X_{sub} with a plain alphanumeric subscript (skips V_{OUT(nom)}, t_{\max off}).
_EQ_VAR_RE = re.compile(r"([A-Za-z])_\{([A-Za-z][A-Za-z0-9]*)\}")


def _standardize_subscripts(md: str) -> str:
    """Render text subscripts that match an equation variable in the same inline
    LaTeX form as the equations (``$V_{comp}$``).

    The equations are the dictionary of real math variables, so genuinely
    non-math subscripts (units, ordinals) are left as ``<sub>`` / plain text.
    Runs after ``_normalize_math``, when the equation LaTeX is clean ``X_{sub}``.
    """
    variables = {}                       # (base, sub) -> "$base_{sub}$"
    for span in _MATH_SPAN_RE.findall(md):
        for base, sub in _EQ_VAR_RE.findall(span):
            if len(sub) >= SUBSCRIPT_MIN_SUB_LEN:
                variables[(base, sub)] = f"${base}_{{{sub}}}$"
    if not variables:
        return md

    # HTML form: a <sub> matching a known variable -> inline LaTeX; others kept.
    md = re.sub(r"(\w)<sub>(\w+)</sub>",
                lambda m: variables.get((m.group(1), m.group(2)), m.group(0)), md)

    # PDF flat form ("VCR", "R BLKlower"): one left-to-right pass over the
    # non-math segments only, longest variable first, so an inserted span is
    # never re-scanned and table/code/math text is untouched.
    concat = {b + s: latex for (b, s), latex in variables.items()}
    alts = sorted(variables, key=lambda bs: -(len(bs[0]) + len(bs[1])))
    flat_re = re.compile(
        r"(?<![A-Za-z])(?:"
        + "|".join(re.escape(b) + r"[ \t]?" + re.escape(s) for b, s in alts)
        + r")(?![A-Za-z0-9])")
    parts = _MATH_SPAN_RE.split(md)      # even idx = non-math text, odd = spans
    for i in range(0, len(parts), 2):
        parts[i] = flat_re.sub(
            lambda m: concat.get(re.sub(r"\s+", "", m.group(0)), m.group(0)),
            parts[i])
    return "".join(parts)


def convert(source: Path, output_dir: Path, ocr: bool, force: bool,
            postprocess: bool) -> Tuple[Path, Path, int, dict]:
    """Run the full PDF/HTML -> Markdown pipeline.

    Returns (source_copy, out_md, image_count, pipeline_stats).
    """
    if not source.is_file():
        raise SystemExit(f"Error: not a file: {source}")
    if source.suffix.lower() not in (".pdf", ".html"):
        raise SystemExit(f"Error: expected a .pdf or .html file, got: {source}")

    stem = source.stem
    suffix = source.suffix.lower()
    output_dir.mkdir(parents=True, exist_ok=True)
    source_copy = output_dir / f"{stem}{suffix}"
    out_md = output_dir / f"{stem}.md"
    images_dir = output_dir / f"{stem}.images"
    image_map_path = output_dir / f"{stem}.image-map.json"

    existing_outputs = [path for path in (out_md, images_dir, image_map_path) if path.exists()]
    if not force and existing_outputs:
        raise SystemExit(
            f"Error: output exists (use --force to overwrite): "
            f"{existing_outputs[0]}"
        )
    if force and images_dir.exists():
        shutil.rmtree(images_dir)
    if force and image_map_path.exists():
        image_map_path.unlink()

    # Copy the original source into the output directory.
    if source.resolve() != source_copy.resolve():
        shutil.copy2(source, source_copy)

    # Convert in a temp dir so the intermediate base64 markdown is discarded.
    image_entries: list[dict] = []
    tmp_dir = Path(tempfile.mkdtemp(prefix="pdf2markdown_"))
    try:
        embedded_md, docling_metadata = run_docling(source, tmp_dir, ocr)
        count = split_images(
            embedded_md,
            out_md,
            images_dir,
            image_entries=image_entries,
            docling_metadata=docling_metadata,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    stats = {"dedupe": {}, "postprocess": {}, "page_furniture": {}}
    if count:
        stats["dedupe"] = deduplicate_images(out_md, images_dir.name)
        update_image_map_for_dedupe(image_entries, stats["dedupe"])
        stats["page_furniture"] = remove_repeated_page_furniture_images(out_md, images_dir.name)

    if postprocess and count:
        import image_postprocess
        print("Post-processing images (OCR + table/text/vector detection)...", flush=True)
        stats["postprocess"] = image_postprocess.postprocess(out_md, images_dir.name)

    # Tidy recurring Markdown artifacts from the converter outputs, then tidy
    # the spaced LaTeX the formula models emit ($$...$$ from PDF
    # --enrich-formula, $...$ from the HTML equation OCR), then standardise
    # equation-variable subscripts in the prose to the same inline-LaTeX form.
    md_text = clean_markdown_text(out_md.read_text(encoding="utf-8"))
    md_text = _normalize_math(md_text)
    md_text = _standardize_subscripts(md_text)
    out_md.write_text(md_text, encoding="utf-8")

    update_image_map_from_markdown(image_entries, out_md, images_dir.name)
    write_image_map(image_map_path, {
        "version": 1,
        "source": source_copy.name,
        "markdown": out_md.name,
        "images_dir": images_dir.name,
        "entries": image_entries,
    })

    return source_copy, out_md, count, stats


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="pdf2markdown",
        description="Convert a PDF to clean Markdown with external image files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("pdf", type=str, metavar="input.pdf | URL",
                        help="Input PDF file or http(s) URL")
    parser.add_argument("output_dir", type=Path, metavar="output_dir", nargs="?",
                        help="Directory for the PDF copy, images, and .md output (default: PDF's directory)")
    parser.add_argument("-f", "--force", action="store_true",
                        help="Overwrite existing .md / .images / image-map output")
    parser.add_argument("--no-ocr", dest="ocr", action="store_false",
                        help="Skip OCR during docling conversion")
    parser.add_argument("--no-postprocess", dest="postprocess", action="store_false",
                        help="Skip the image post-processing pass (keep all images as PNG)")
    args = parser.parse_args()

    download_dir = None
    if is_url(args.pdf):
        default_output = Path.cwd()
    else:
        input_path = Path(args.pdf).resolve()
        default_output = input_path.parent

    try:
        if is_url(args.pdf):
            download_dir = Path(tempfile.mkdtemp(prefix="pdf2markdown_dl_"))
            source = find_source(args.pdf)
            if source is not None:
                print(f"Fetching {source.name} HTML datasheet ...", flush=True)
                input_path, _ = source.fetch(args.pdf, download_dir)
            else:
                input_path = download_pdf(args.pdf, download_dir)
        output_dir = args.output_dir if args.output_dir is not None else default_output
        source_copy, out_md, count, stats = convert(
            input_path, output_dir, ocr=args.ocr, force=args.force,
            postprocess=args.postprocess,
        )
    finally:
        if download_dir is not None:
            shutil.rmtree(download_dir, ignore_errors=True)
    pp = stats.get("postprocess", {})
    dedupe = stats.get("dedupe", {})
    page_furniture = stats.get("page_furniture", {})

    images_dir = out_md.parent / (out_md.stem + ".images")
    remaining = len(list(images_dir.iterdir())) if images_dir.is_dir() else 0
    # Equation count from the actual output: docling --enrich-formula emits
    # $$...$$ blocks (PDF), while the HTML route inlines image equations as
    # $...$ via LaTeX-OCR (pp["equation"]).
    md_text = out_md.read_text(encoding="utf-8") if out_md.is_file() else ""
    equation_count = len(re.findall(r"\$\$.+?\$\$", md_text, re.S)) + pp.get("equation", 0)

    print("\nDone")
    print(f"  Source   : {source_copy}")
    print(f"  Markdown : {out_md}")
    print(f"  Image map: {out_md.parent / (out_md.stem + '.image-map.json')}")
    print(f"  Images   : {count} extracted, {remaining} kept in {images_dir}/")
    if equation_count:
        print(f"  Equations: {equation_count} formula(s) -> LaTeX")
    if dedupe:
        print(f"  Dedupe   : {dedupe.get('duplicate_refs', 0)} duplicate ref(s), "
              f"{dedupe.get('removed_files', 0)} file(s) removed")
    if page_furniture and page_furniture.get("removed_refs", 0):
        print(f"  Furniture: {page_furniture.get('removed_refs', 0)} repeated tiny image ref(s) removed")
    if pp:
        print(f"  Inlined  : {pp.get('text', 0)} text image(s)")
        print(f"  Vector   : {pp.get('diagram', 0)} diagram(s) -> SVG")
        print(f"  Kept PNG : {pp.get('photo', 0)} photo(s)")
        vdr = pp.get("visual_dedupe_refs", 0)
        if vdr:
            print(f"  Visual dedup: {vdr} duplicate diagram ref(s), "
                  f"{pp.get('visual_dedupe_files', 0)} PNG(s) removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
