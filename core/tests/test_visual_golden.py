"""Pixel-golden regression tests for the pinned renderer.

Complements the op-level goldens (test_golden_remarkable.py): ops catch
content-stream changes, pixels catch anything that alters what the reader
or renderer actually puts on the page — across several source formats, not
just reMarkable.

Goldens are PNGs rendered at a fixed dpi (deterministic: PNG has no
timestamps). Regenerate deliberately with: uv run pytest --update-goldens
A golden diff means the rendering changed — never regenerate to silence a
red test.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from PIL import Image

from inkterop import formats
from inkterop.formats.base import Fidelity
from inkterop.visual.diff import compare
from inkterop.visual.png import PngWriter

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden" / "visual"
DPI = 96
# Calibrated (visual/README.md): noise floor is exactly 100%; the hardest
# bug (one dropped stroke on a dense page) scores 99.986%.
MIN_INK_MATCH = 0.9999

CASES = [
    "remarkable/fineliner-pencil-colors.rm",
    "remarkable/calligraphy-marker-paintbrush-shader.rm",
    "remarkable/landscape-highlighter.rm",
    "goodnotes/gn-mac-mixed-pens.goodnotes",
    "saber/saber-mac-pens-text.sba",
]


def _render_pngs(fixture: Path, out: Path) -> list[Path]:
    """Render every page; returns the files PngWriter produced."""
    reader = formats.reader_for(fixture)
    assert reader is not None, f"no reader for {fixture}"
    doc = reader.read(fixture)
    PngWriter().write(doc, out, Fidelity.EXACT, {"dpi": DPI})
    pages = [out]
    n = 2
    while (p := out.with_stem(f"{out.stem}-{n}")).exists():
        pages.append(p)
        n += 1
    return pages


@pytest.mark.parametrize("rel", CASES)
def test_pixel_golden(rel: str, request: pytest.FixtureRequest) -> None:
    fixture = FIXTURES / rel
    golden = GOLDEN / (Path(rel).name + ".png")

    if request.config.getoption("--update-goldens"):
        GOLDEN.mkdir(parents=True, exist_ok=True)
        _render_pngs(fixture, golden)
        pytest.skip(f"golden updated: {golden.name}")

    assert golden.exists(), (
        f"missing golden {golden}; run: uv run pytest --update-goldens")
    with tempfile.TemporaryDirectory() as td:
        pages = _render_pngs(fixture, Path(td) / golden.name)
        for page in pages:
            gold = GOLDEN / page.name
            assert gold.exists(), f"missing golden page {gold.name}"
            result = compare(Image.open(gold), Image.open(page),
                             mode="strict")
            assert result.ink_match_ratio >= MIN_INK_MATCH, (
                f"{page.name}: render drifted from golden: ink-match "
                f"{result.ink_match_ratio:.4%} < {MIN_INK_MATCH:.2%} "
                f"({result.n_diff_pixels} px differ)")
