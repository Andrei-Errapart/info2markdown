"""Picture->table reconstruction is disabled — docling owns tables.

Extracted images are figures (diagrams, plots, schematics); reconstructing a
table from them via grid-line OCR reliably garbled block diagrams / pin-outs /
plots, and no extracted picture was ever a real table (docling emits genuine
tables as Markdown directly). Guard against re-introduction.
"""

import image_postprocess


def test_imagetype_has_no_table_member():
    assert "TABLE" not in image_postprocess.ImageType.__members__


def test_table_reconstruction_helpers_removed():
    for name in ("detect_table_structure", "table_rows_to_markdown",
                 "_looks_like_regular_table"):
        assert not hasattr(image_postprocess, name), f"{name} should be removed"
