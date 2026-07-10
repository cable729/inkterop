"""Calibrate visualdiff thresholds: noise floor vs known-bug sensitivity.

Runs three experiment groups and prints a table; the chosen defaults in
`inkterop/visual/diff.py` + the pass thresholds in the visual tests must
sit in the gap this measures. Method + latest results are recorded in
`src/inkterop/visual/README.md`.

  uv run python scripts/calibrate_visualdiff.py [--corpus DIR]

Groups:
  A. Noise floor — same document rendered twice; rendered and re-registered
     (crop+rescale round); rendered at 2x dpi vs 1x registered. All of
     these SHOULD pass any threshold we pick.
  B. Injected bugs — width x2, dropped stroke, color swap, opacity halved,
     small translation. All SHOULD fail.
  C. Cross-app floor (needs corpus) — our render vs the app's own export
     of the same document. Scores what "correct but foreign rasterizer"
     looks like in registered mode.
"""
from __future__ import annotations

import argparse
import copy
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image  # noqa: E402

from inkterop import formats, ir  # noqa: E402
from inkterop.formats.base import Fidelity  # noqa: E402
from inkterop.visual.diff import compare, compare_pdfs  # noqa: E402
from inkterop.visual.png import PngWriter  # noqa: E402
from inkterop.visual.raster import pdf_pages_to_images  # noqa: E402

CORE = Path(__file__).resolve().parents[1]
FIXTURES = CORE / "tests" / "fixtures"
DPI = 96

ROWS: list[tuple[str, str, float, float]] = []  # group, name, match, ink_match


def record(group: str, name: str, r) -> None:
    ROWS.append((group, name, r.match_ratio, r.ink_match_ratio))


def render(doc: ir.Document, td: Path, name: str, dpi: int = DPI) -> Image.Image:
    out = td / f"{name}.png"
    PngWriter().write(doc, out, Fidelity.EXACT, {"dpi": dpi})
    return Image.open(out)


def read(rel: str) -> ir.Document:
    path = FIXTURES / rel
    reader = formats.reader_for(path)
    assert reader, rel
    return reader.read(path)


# --- bug injectors -----------------------------------------------------------

def bug_width_x2(doc: ir.Document) -> ir.Document:
    doc = copy.deepcopy(doc)
    for page in doc.pages:
        for s in page.strokes():
            if ir.Channel.WIDTH in s.channels:
                s.channels[ir.Channel.WIDTH] = [
                    w * 2 for w in s.channels[ir.Channel.WIDTH]]
            if s.appearance and s.appearance.width:
                s.appearance.width *= 2
    return doc


def bug_drop_stroke(doc: ir.Document) -> ir.Document:
    doc = copy.deepcopy(doc)
    for page in doc.pages:
        for layer in page.layers:
            if layer.strokes:
                del layer.strokes[len(layer.strokes) // 2]
                return doc
    return doc


def bug_color_swap(doc: ir.Document) -> ir.Document:
    """Swap R and G on every stroke (the Notability byte-order failure mode)."""
    doc = copy.deepcopy(doc)
    for page in doc.pages:
        for s in page.strokes():
            c = s.color
            s.color = ir.Color(c.g, c.r, c.b, c.a)
            if s.appearance and s.appearance.color:
                ac = s.appearance.color
                s.appearance.color = ir.Color(ac.g, ac.r, ac.b, ac.a)
    return doc


def bug_opacity_half(doc: ir.Document) -> ir.Document:
    doc = copy.deepcopy(doc)
    for page in doc.pages:
        for s in page.strokes():
            if s.appearance is not None:
                s.appearance.opacity = (s.appearance.opacity or 1.0) * 0.5
    return doc


def bug_translate(doc: ir.Document) -> ir.Document:
    """Shift all ink by ~1% of page width (registration/geometry bug)."""
    doc = copy.deepcopy(doc)
    for page in doc.pages:
        dx = (page.bounds.x_max - page.bounds.x_min) * 0.01
        for s in page.strokes():
            s.x = [x + dx for x in s.x]
    return doc


BUGS = [("width x2", bug_width_x2), ("dropped stroke", bug_drop_stroke),
        ("color swap R<->G", bug_color_swap),
        ("opacity halved", bug_opacity_half),
        ("translate 1%", bug_translate)]

DOCS = ["remarkable/fineliner-pencil-colors.rm",
        "goodnotes/gn-mac-mixed-pens.goodnotes"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", type=Path,
                    default=CORE.parent / "corpus")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as td_str:
        td = Path(td_str)
        for rel in DOCS:
            doc = read(rel)
            name = Path(rel).stem
            base = render(doc, td, f"{name}-base")

            # A: noise floor
            record("A noise", f"{name}: rerender",
                   compare(base, render(doc, td, f"{name}-again")))
            record("A noise", f"{name}: self registered",
                   compare(base, base, mode="registered"))
            hi = render(doc, td, f"{name}-2x", dpi=DPI * 2)
            record("A noise", f"{name}: 2x dpi registered",
                   compare(base, hi, mode="registered"))

            # B: injected bugs (strict, same raster geometry)
            for bug_name, fn in BUGS:
                record("B bug", f"{name}: {bug_name}",
                       compare(base, render(fn(doc), td,
                                            f"{name}-{bug_name[:8]}")))

        # C: cross-app floor from corpus pairs
        pairs = [
            (FIXTURES / "goodnotes/gn-mac-mixed-pens.goodnotes",
             args.corpus / "gn-mac-mixed-pens.app-export.pdf"),
            (FIXTURES / "saber/saber-mac-pens-text.sba",
             args.corpus / "saber-mac-pens-text.app-export.pdf"),
        ]
        for fixture, app_pdf in pairs:
            if not app_pdf.exists():
                print(f"skip C: {app_pdf} not present", file=sys.stderr)
                continue
            reader = formats.reader_for(fixture)
            doc = reader.read(fixture)
            ours = td / (fixture.stem + ".ours.pdf")
            from inkterop.render.pdf import PdfWriter
            PdfWriter().write(doc, ours, Fidelity.EXACT)
            for i, r in enumerate(compare_pdfs(app_pdf, ours,
                                               mode="registered", dpi=DPI)):
                record("C cross-app", f"{fixture.stem} p{i + 1}", r)

    print(f"\n{'group':<12} {'case':<42} {'match':>9} {'ink-match':>9}")
    for group, name, m, im in ROWS:
        print(f"{group:<12} {name:<42} {m:>8.4%} {im:>8.4%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
