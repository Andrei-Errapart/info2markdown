"""The HTML route's LatexOcr delegates to the shared UniMERNet recognizer."""

from PIL import Image

import image_postprocess


def test_latexocr_to_latex_delegates(tmp_path, monkeypatch):
    import unimernet_formula
    monkeypatch.setattr(unimernet_formula, "recognize", lambda img: "V_{comp}")
    p = tmp_path / "eq.png"
    Image.new("RGB", (20, 10), "white").save(p)
    assert image_postprocess.LatexOcr().to_latex(p) == "V_{comp}"
