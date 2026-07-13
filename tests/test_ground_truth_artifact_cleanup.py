import base64
import json
import re
from pathlib import Path

import pdf2markdown
from pdf2markdown import (
    clean_markdown_text,
    remove_repeated_page_furniture_images,
    split_images,
)
from tests.fixtures.generate_duplicate_fixture import make_png


def test_docling_callable_caption_text_is_used_for_image_map_alt(tmp_path: Path) -> None:
    data = make_png(12, 12, [(10, 20, 30)])
    encoded = base64.b64encode(data).decode("ascii")
    embedded_md = tmp_path / "doc.embedded.md"
    embedded_md.write_text(
        f"![<bound method FloatingItem.caption_text data:image/png;base64,{encoded}>]"
        f"(data:image/png;base64,{encoded})\n",
        encoding="utf-8",
    )
    out_md = tmp_path / "doc.md"
    images_dir = tmp_path / "doc.images"
    entries = []

    split_images(
        embedded_md,
        out_md,
        images_dir,
        image_entries=entries,
        docling_metadata=[{"alt": lambda: "Figure 1. Block diagram"}],
    )

    assert entries[0]["alt"] == "Figure 1. Block diagram"
    assert "![Image](" in out_md.read_text(encoding="utf-8")


def test_clean_markdown_text_removes_common_ground_truth_artifacts() -> None:
    md = "\n".join([
        "## AND90149/D",
        "Normal text /C0069 and /C0109 F.",
        "- \uf0b7 duplicated bullet",
        "Output \uf0a3 12-bit and input \uf0b3 16-bit with \uf0b1 1 and 10\uf0b0 CRA.",
        "Patent -Marking and technical - documentation are as -is.",
        "Trailing text.",
        "",
        "61",
    ])

    out = clean_markdown_text(md)

    assert "AND90149/D" not in out
    assert "\u00a9" in out
    assert "\u00b5 F" in out
    assert "- duplicated bullet" in out
    assert "\u2264 12-bit" in out
    assert "\u2265 16-bit" in out
    assert "\u00b1 1" in out
    assert "10\u00b0 CRA" in out
    assert "Patent-Marking" in out
    assert "technical-documentation" in out
    assert "as-is" in out
    assert not out.rstrip().endswith("61")


def test_clean_markdown_text_demotes_table_caption_headings() -> None:
    md = "\n".join([
        "## Table 5. TRIGGER MODES",
        "### Table 4.2  DC Characteristics (1/2)",
        "## Table of Contents",
        "## THERMAL CHARACTERISTICS",
    ])

    out = clean_markdown_text(md)

    # ``Table N.`` captions lose their heading markers but keep the caption text
    assert "\n".join(["Table 5. TRIGGER MODES",
                      "Table 4.2  DC Characteristics (1/2)"]) in out
    assert re.search(r"(?m)^#{1,6}\s+Table \d+\.", out) is None
    # real headings (no ``Table N.`` caption shape) are left untouched
    assert "## Table of Contents" in out
    assert "## THERMAL CHARACTERISTICS" in out


def test_clean_markdown_text_keeps_fenced_code_verbatim() -> None:
    md = "```text\n/C0069\nPatent -Marking\n```\n\nOutside /C0069\n"

    out = clean_markdown_text(md)

    assert "```text\n/C0069\nPatent -Marking\n```" in out
    assert "Outside \u00a9" in out


def test_clean_markdown_text_drops_long_bare_number_runs() -> None:
    md = "Before\n\n1\n2\n3 4\n10|\n14 15\n16\n\nAfter\n"

    out = clean_markdown_text(md)

    assert out == "Before\n\n\nAfter\n"


def test_repeated_tiny_page_furniture_images_are_removed(tmp_path: Path) -> None:
    images_dir = tmp_path / "doc.images"
    images_dir.mkdir()
    logo = images_dir / "logo.png"
    figure = images_dir / "figure.png"
    logo.write_bytes(make_png(160, 25, [(10, 10, 10), (255, 255, 255)]))
    figure.write_bytes(make_png(160, 80, [(10, 10, 10), (255, 255, 255)]))
    md_path = tmp_path / "doc.md"
    md_path.write_text(
        "\n".join([
            "![logo](doc.images/logo.png)",
            "Body",
            "![logo again](doc.images/logo.png)",
            "![figure](doc.images/figure.png)",
        ]),
        encoding="utf-8",
    )

    stats = remove_repeated_page_furniture_images(md_path, images_dir.name)

    assert stats == {"removed_refs": 2, "removed_files": 1}
    text = md_path.read_text(encoding="utf-8")
    assert "logo.png" not in text
    assert "figure.png" in text
    assert not logo.exists()
    assert figure.exists()


def test_convert_applies_cleanup_before_writing_image_map(tmp_path: Path, monkeypatch) -> None:
    logo_data = make_png(160, 25, [(10, 10, 10), (255, 255, 255)])
    encoded = base64.b64encode(logo_data).decode("ascii")
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 fake")

    def fake_run_docling(source: Path, out_dir: Path, ocr: bool):
        embedded = out_dir / "doc.md"
        embedded.write_text(
            "\n".join([
                "## AND90149/D",
                f"![logo](data:image/png;base64,{encoded})",
                "Body /C0069",
                f"![logo](data:image/png;base64,{encoded})",
                "",
                "2",
            ]),
            encoding="utf-8",
        )
        return embedded, []

    monkeypatch.setattr(pdf2markdown, "run_docling", fake_run_docling)

    _, out_md, _, stats = pdf2markdown.convert(
        src,
        tmp_path / "out",
        ocr=False,
        force=False,
        postprocess=False,
    )

    assert stats["page_furniture"] == {"removed_refs": 2, "removed_files": 1}
    assert out_md.read_text(encoding="utf-8") == "\nBody \u00a9\n"
    payload = json.loads((out_md.parent / "doc.image-map.json").read_text(encoding="utf-8"))
    assert [entry["status"] for entry in payload["entries"]] == ["inlined", "inlined"]
