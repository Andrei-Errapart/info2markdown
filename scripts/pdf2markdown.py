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
    -f, --force          Overwrite existing <stem>.md / <stem>.images/ in output dir
        --no-ocr         Skip OCR during docling conversion (faster; text-only PDFs)
        --no-postprocess Skip the image post-processing pass (keep all images as PNG)
    -h, --help           Show this help
"""

import argparse
import base64
import hashlib
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import unquote, urlsplit

from datasheet_sources import find_source

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


def find_docling() -> str:
    """Locate the docling CLI, preferring the one beside the current interpreter."""
    candidate = Path(sys.executable).parent / "docling"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("docling")
    if found:
        return found
    raise SystemExit(
        "Error: docling not found. Run via the 'pdf2markdown' wrapper, or "
        "`pip install -r scripts/requirements.txt` in your environment."
    )


def build_docling_cmd(docling: str, source: Path, out_dir: Path, ocr: bool) -> list:
    """Build the docling CLI argv for a .pdf or .html source."""
    cmd = [docling, str(source), "--to", "md", "--image-export-mode", "embedded"]
    if source.suffix.lower() == ".pdf":
        if ocr:
            cmd.append("--ocr")
        # --enrich-formula: detect formula regions and emit them as LaTeX. PDF
        # equations are vector/text (not images), so this is the only way to get
        # them; the HTML route handles its image equations via LaTeX-OCR instead.
        cmd += ["--tables", "--table-mode", "accurate", "--enrich-formula"]
    cmd += ["--output", str(out_dir)]
    return cmd


def run_docling_html(source: Path, out_dir: Path) -> Path:
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
    return out_md


def run_docling(docling: str, source: Path, out_dir: Path, ocr: bool) -> Path:
    """Run docling embedded-image conversion; return the produced .md file."""
    if source.suffix.lower() == ".html":
        return run_docling_html(source, out_dir)
    cmd = build_docling_cmd(docling, source, out_dir, ocr)
    print(f"Converting {source.name} with docling...", flush=True)
    subprocess.run(cmd, check=True)

    produced = out_dir / f"{source.stem}.md"
    if produced.is_file():
        return produced
    # Fall back to whatever single .md docling emitted.
    candidates = list(out_dir.glob("*.md"))
    if len(candidates) == 1:
        return candidates[0]
    raise SystemExit(
        f"Error: could not locate docling output .md in {out_dir} "
        f"(found: {[c.name for c in candidates]})"
    )


def split_images(source_md: Path, out_md: Path, images_dir: Path) -> int:
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
        return f"![{alt}]({images_dir.name}/{fname})"

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


def _normalize_math(md: str) -> str:
    """Normalise the LaTeX inside every ``$$...$$`` and ``$...$`` span (only
    spans that look like math, to leave a stray ``$`` in prose alone)."""
    def norm(inner: str) -> str:
        return _normalize_latex(inner) if re.search(r"[\\_^{}]", inner) else inner
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

    if not force and (out_md.exists() or images_dir.exists()):
        raise SystemExit(
            f"Error: output exists (use --force to overwrite): "
            f"{out_md if out_md.exists() else images_dir}"
        )
    if force and images_dir.exists():
        shutil.rmtree(images_dir)

    docling = find_docling()

    # Copy the original source into the output directory.
    if source.resolve() != source_copy.resolve():
        shutil.copy2(source, source_copy)

    # Convert in a temp dir so the intermediate base64 markdown is discarded.
    tmp_dir = Path(tempfile.mkdtemp(prefix="pdf2markdown_"))
    try:
        embedded_md = run_docling(docling, source, tmp_dir, ocr)
        count = split_images(embedded_md, out_md, images_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    stats = {"dedupe": {}, "postprocess": {}}
    if count:
        stats["dedupe"] = deduplicate_images(out_md, images_dir.name)

    if postprocess and count:
        import image_postprocess
        print("Post-processing images (OCR + table/text/vector detection)...", flush=True)
        stats["postprocess"] = image_postprocess.postprocess(out_md, images_dir.name)

    # Tidy the spaced LaTeX the formula models emit ($$...$$ from PDF
    # --enrich-formula, $...$ from the HTML equation OCR), then standardise
    # equation-variable subscripts in the prose to the same inline-LaTeX form.
    md_text = _normalize_math(out_md.read_text(encoding="utf-8"))
    md_text = _standardize_subscripts(md_text)
    out_md.write_text(md_text, encoding="utf-8")

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
                        help="Overwrite existing .md / .images output")
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
    print(f"  Images   : {count} extracted, {remaining} kept in {images_dir}/")
    if equation_count:
        print(f"  Equations: {equation_count} formula(s) -> LaTeX")
    if dedupe:
        print(f"  Dedupe   : {dedupe.get('duplicate_refs', 0)} duplicate ref(s), "
              f"{dedupe.get('removed_files', 0)} file(s) removed")
    if pp:
        print(f"  Inlined  : {pp.get('table', 0)} table(s), {pp.get('text', 0)} text image(s)")
        print(f"  Vector   : {pp.get('diagram', 0)} diagram(s) -> SVG")
        print(f"  Kept PNG : {pp.get('photo', 0)} photo(s)")
        vdr = pp.get("visual_dedupe_refs", 0)
        if vdr:
            print(f"  Visual dedup: {vdr} duplicate diagram ref(s), "
                  f"{pp.get('visual_dedupe_files', 0)} PNG(s) removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
