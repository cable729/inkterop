"""Erased-stroke audit: verify every stroke our readers emit is actually
rendered in the app's own PDF export of the same calibration page.

For each calibration pair (native file + app export, see
corpus/calibration/MANIFEST.md) we map every parsed stroke point into the
app's raster and test for non-background pixels within a small radius.
A stroke with low coverage is ink we render that the app does not — the
signature of erased/superseded strokes leaking through a reader.

Lessons baked in (2026-07-10 first run):
- background must be sampled per app EXCLUDING ink colors — wide
  highlighter/marker rows are common enough to poll as "background"
  (GoodNotes grid, yellow highlighters) and false-flag whole rows;
- very faint marks (Saber pencil dot) sit under any sane threshold —
  eyeball the crops before calling something erased;
- GoodNotes field-14=1 re-records looked like erase tombstones but the
  app renders those items; this script is what refuted it.

Run from core/: uv run python scripts/erase_audit.py
"""
import os
from pathlib import Path

import pypdfium2 as pdfium

from inkterop.formats import reader_for

# corpus/ is machine-local and gitignored; override when running from a
# worktree that doesn't have it.
CAL = Path(os.environ.get("INKTEROP_CORPUS", "../corpus")) / "calibration"

# (name, native, app pdf, page index, app page width in native units,
#  background colors — ink colors must NOT be listed here)
CASES = [
    ("notability", CAL / "notability-calibration.ntb",
     CAL / "notability-calibration.app-export.pdf", 0,
     612.0, [(254, 254, 254), (185, 200, 221)]),
    # Known false positive: stroke #13, a 9-pt pencil dot at (582,144),
    # renders as a near-white blob (verified by zoomed crop 2026-07-10).
    ("saber", CAL / "saber-calibration.sbn2",
     CAL / "saber-calibration.app-export.pdf", 0,
     1000.0, [(252, 252, 252)]),
    ("nebo", CAL / "nebo-calibration.nebo",
     CAL / "nebo-calibration.app-export.pdf", 0,
     210.0, [(255, 255, 255)]),  # native mm
    # GoodNotes: app page = the full 834.24pt-wide paper; cream + grid bg.
    ("goodnotes", CAL / "goodnotes-calibration.goodnotes",
     CAL / "goodnotes-calibration.app-export.pdf", 1,
     834.24, [(248, 247, 233), (208, 210, 211)]),
]

RENDER_SCALE = 3.0
RADIUS = 6
COLOR_DIST = 60  # summed |dr|+|dg|+|db| distance from every bg color
LOW = 0.8


def audit(name, native, pdf_path, page_idx, page_w_units, bg):
    doc = reader_for(native).read(native)
    strokes = list(doc.pages[page_idx].strokes())
    page = pdfium.PdfDocument(pdf_path)[page_idx]
    img = page.render(scale=RENDER_SCALE).to_pil().convert("RGB")
    # native units -> app-render px (uniform fit of the page width)
    k = page.get_size()[0] / page_w_units * RENDER_SCALE

    def covered(x, y):
        px, py = int(x * k), int(y * k)
        for dx in range(-RADIUS, RADIUS + 1):
            for dy in range(-RADIUS, RADIUS + 1):
                if 0 <= px + dx < img.width and 0 <= py + dy < img.height:
                    p = img.getpixel((px + dx, py + dy))
                    if all(sum(abs(a - b) for a, b in zip(p, c)) > COLOR_DIST
                           for c in bg):
                        return True
        return False

    print(f"=== {name}: {len(strokes)} strokes")
    clean = True
    for i, s in enumerate(strokes):
        pts = list(zip(s.x, s.y))
        frac = sum(covered(x, y) for x, y in pts) / max(1, len(pts))
        if frac < LOW:
            clean = False
            xs, ys = s.x, s.y
            print(f"  LOW #{i} {s.tool.family.value:12s} cov={frac:.2f} "
                  f"n={len(pts)} bbox=[{min(xs):.0f},{min(ys):.0f},"
                  f"{max(xs):.0f},{max(ys):.0f}]")
    if clean:
        print("  clean — every parsed stroke renders in the app's export")


if __name__ == "__main__":
    for case in CASES:
        audit(*case)
