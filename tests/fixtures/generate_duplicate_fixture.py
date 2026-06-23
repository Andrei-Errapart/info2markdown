#!/usr/bin/env python3
"""Generate deterministic duplicate-image fixtures for pdf2markdown tests."""

import argparse
import base64
import hashlib
import json
import random
import struct
import zlib
from io import BytesIO
from pathlib import Path
from typing import Dict, List

DEFAULT_SEED = 20260623
STEM = "duplicate_release_notes"


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(kind + data) & 0xffffffff
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def make_png(width: int, height: int, colors: List[tuple[int, int, int]]) -> bytes:
    """Create a small deterministic RGB PNG using only the standard library."""
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            color = colors[(x // 12 + y // 12) % len(colors)]
            row.extend(color)
        rows.append(b"\x00" + bytes(row))

    raw = b"".join(rows)
    png = [
        b"\x89PNG\r\n\x1a\n",
        _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
        _png_chunk(b"IDAT", zlib.compress(raw, level=9)),
        _png_chunk(b"IEND", b""),
    ]
    return b"".join(png)


def _data_uri(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _split_name(index: int, data: bytes) -> str:
    digest = hashlib.md5(data).hexdigest()[:8]
    return f"image_{index:03d}_{digest}.png"


def _canonical_name(data: bytes) -> str:
    return f"{hashlib.sha256(data).hexdigest()}.png"


def build_fixture(seed: int = DEFAULT_SEED) -> Dict[str, object]:
    rng = random.Random(seed)
    logo = make_png(96, 36, [(20, 54, 76), (230, 238, 242), (64, 140, 180)])
    footer = make_png(128, 20, [(40, 40, 44), (210, 210, 210)])
    badge = make_png(72, 48, [(116, 48, 44), (245, 226, 178), (42, 96, 90)])

    refs = []
    for page in range(1, 5):
        accent = rng.randrange(60, 210)
        unique = make_png(
            112,
            44,
            [(accent, 80, 120), (230, 235, 220), (30, 60 + page * 20, 100)],
        )
        refs.extend([
            ("Product logo", logo),
            (f"Section {page} diagram", unique),
            ("Compatibility badge", badge),
            ("Repeated footer", footer),
            ("Product logo", logo),
        ])

    embedded_lines = [f"# Synthetic Release Notes {seed}", ""]
    expected_lines = [f"# Synthetic Release Notes {seed}", ""]
    canonical_by_original = {}
    groups: Dict[str, List[str]] = {}

    for index, (alt, data) in enumerate(refs, 1):
        original = _split_name(index, data)
        canonical = _canonical_name(data)
        canonical_by_original[original] = canonical
        groups.setdefault(canonical, []).append(original)
        embedded_lines.extend([
            f"## Entry {index:02d}",
            f"![{alt}]({_data_uri(data)})",
            "",
        ])
        expected_lines.extend([
            f"## Entry {index:02d}",
            f"![{alt}]({STEM}.images/{canonical})",
            "",
        ])

    duplicate_groups = {
        canonical: originals
        for canonical, originals in groups.items()
        if len(originals) > 1
    }
    manifest = {
        "seed": seed,
        "hash_algorithm": "sha256",
        "source_image_refs": len(refs),
        "unique_images": len(groups),
        "duplicate_refs": len(refs) - len(groups),
        "expected_files": sorted(groups),
        "canonical_by_original": canonical_by_original,
        "duplicate_groups": duplicate_groups,
    }
    return {
        "embedded_markdown": "\n".join(embedded_lines),
        "expected_markdown": "\n".join(expected_lines),
        "manifest": manifest,
        "pdf_refs": refs,
    }


def write_pdf(path: Path, refs: List[tuple[str, bytes]], seed: int) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter
    for page in range(4):
        c.setFont("Helvetica-Bold", 14)
        c.drawString(54, height - 54, f"Synthetic Release Notes {seed}")
        c.setFont("Helvetica", 9)
        c.drawString(54, height - 72, f"Page {page + 1}: generated duplicate-image fixture")
        y = height - 120
        for alt, data in refs[page * 5:(page + 1) * 5]:
            c.drawString(54, y + 8, alt)
            c.drawImage(ImageReader(BytesIO(data)), 210, y, width=120, height=45, mask="auto")
            y -= 82
        c.showPage()
    c.save()


def write_fixture(output_dir: Path, seed: int = DEFAULT_SEED, pdf: bool = True) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    fixture = build_fixture(seed)
    paths = {
        "embedded_md": output_dir / f"{STEM}.embedded.md",
        "expected_md": output_dir / f"{STEM}.expected.md",
        "manifest": output_dir / "duplicates.json",
        "pdf": output_dir / f"{STEM}.pdf",
    }
    paths["embedded_md"].write_text(fixture["embedded_markdown"], encoding="utf-8")
    paths["expected_md"].write_text(fixture["expected_markdown"], encoding="utf-8")
    paths["manifest"].write_text(
        json.dumps(fixture["manifest"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if pdf:
        write_pdf(paths["pdf"], fixture["pdf_refs"], seed)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--no-pdf", action="store_true", help="Skip ReportLab PDF export")
    args = parser.parse_args()

    write_fixture(args.output_dir, seed=args.seed, pdf=not args.no_pdf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
