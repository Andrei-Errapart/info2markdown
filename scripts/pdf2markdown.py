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
files and rewrites the links to point at them.

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
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Tuple

# Matches docling's embedded images: ![alt](data:image/<fmt>;base64,<data>)
# (ported from /home/andrei/2026/mdoc/divide_png_md)
IMAGE_RE = re.compile(
    r'!\[([^\]]*)\]\(data:image/(png|jpeg|jpg);base64,([A-Za-z0-9+/=]+)\)'
)


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


def run_docling(docling: str, pdf: Path, out_dir: Path, ocr: bool) -> Path:
    """Run docling embedded-image conversion; return the produced .md file."""
    cmd = [
        docling, str(pdf),
        "--to", "md",
        "--image-export-mode", "embedded",
    ]
    if ocr:
        cmd.append("--ocr")
    cmd += ["--tables", "--table-mode", "accurate", "--output", str(out_dir)]

    print(f"Converting {pdf.name} with docling...", flush=True)
    subprocess.run(cmd, check=True)

    produced = out_dir / f"{pdf.stem}.md"
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


def convert(pdf: Path, output_dir: Path, ocr: bool, force: bool,
            postprocess: bool) -> Tuple[Path, Path, int, dict]:
    """Run the full PDF -> Markdown pipeline.

    Returns (pdf_copy, out_md, image_count, postprocess_counts).
    """
    if not pdf.is_file():
        raise SystemExit(f"Error: not a file: {pdf}")
    if pdf.suffix.lower() != ".pdf":
        raise SystemExit(f"Error: expected a .pdf file, got: {pdf}")

    stem = pdf.stem
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_copy = output_dir / f"{stem}.pdf"
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

    # Copy the original PDF into the output directory.
    if pdf.resolve() != pdf_copy.resolve():
        shutil.copy2(pdf, pdf_copy)

    # Convert in a temp dir so the intermediate base64 markdown is discarded.
    tmp_dir = Path(tempfile.mkdtemp(prefix="pdf2markdown_"))
    try:
        embedded_md = run_docling(docling, pdf, tmp_dir, ocr)
        count = split_images(embedded_md, out_md, images_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    pp_counts: dict = {}
    if postprocess and count:
        import image_postprocess
        print("Post-processing images (OCR + table/text/vector detection)...", flush=True)
        pp_counts = image_postprocess.postprocess(out_md, images_dir.name)

    return pdf_copy, out_md, count, pp_counts


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="pdf2markdown",
        description="Convert a PDF to clean Markdown with external image files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("pdf", type=Path, metavar="input.pdf", help="Input PDF file")
    parser.add_argument("output_dir", type=Path, metavar="output_dir", nargs="?",
                        help="Directory for the PDF copy, images, and .md output (default: PDF's directory)")
    parser.add_argument("-f", "--force", action="store_true",
                        help="Overwrite existing .md / .images output")
    parser.add_argument("--no-ocr", dest="ocr", action="store_false",
                        help="Skip OCR during docling conversion")
    parser.add_argument("--no-postprocess", dest="postprocess", action="store_false",
                        help="Skip the image post-processing pass (keep all images as PNG)")
    args = parser.parse_args()

    output_dir = args.output_dir if args.output_dir is not None else args.pdf.resolve().parent
    pdf_copy, out_md, count, pp = convert(
        args.pdf.resolve(), output_dir, ocr=args.ocr, force=args.force,
        postprocess=args.postprocess,
    )

    images_dir = out_md.parent / (out_md.stem + ".images")
    remaining = len(list(images_dir.iterdir())) if images_dir.is_dir() else 0

    print("\nDone")
    print(f"  PDF copy : {pdf_copy}")
    print(f"  Markdown : {out_md}")
    print(f"  Images   : {count} extracted, {remaining} kept in {images_dir}/")
    if pp:
        print(f"  Inlined  : {pp.get('table', 0)} table(s), {pp.get('text', 0)} text image(s)")
        print(f"  Vector   : {pp.get('diagram', 0)} diagram(s) -> SVG")
        print(f"  Kept PNG : {pp.get('photo', 0)} photo(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
