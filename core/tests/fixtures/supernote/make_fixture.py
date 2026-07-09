"""Generate the synthetic Supernote X-series .note fixtures in this dir.

The container machinery lives in inkterop.formats.supernote.writer (it
was promoted from this script); this script keeps only the rect-based
bitmap synthesis so the committed fixtures stay byte-identical.

Run: uv run python tests/fixtures/supernote/make_fixture.py
"""
from __future__ import annotations

from pathlib import Path

from inkterop.formats.supernote.writer import (
    BG,
    BLACK,
    DARK_GRAY,
    GRAY,
    HEIGHT,
    LANDSCAPE,
    PORTRAIT,
    WIDTH,
    build_note,
    rle_encode,
)

HERE = Path(__file__).parent


def rect_bitmap(width: int, height: int, rects) -> bytes:
    """Row-major code array for background + non-overlapping solid rects.

    rects: (colorcode, x0, y0, x1, y1) with exclusive x1/y1.
    """
    codes = bytearray()
    for y in range(height):
        row = bytearray([BG]) * width
        for code, x0, y0, x1, y1 in rects:
            if y0 <= y < y1:
                row[x0:x1] = bytes([code]) * (x1 - x0)
        codes += row
    return rle_encode(bytes(codes))


def build(pages: list[tuple[list, str]]) -> bytes:
    bitmaps = []
    for rects, orientation in pages:
        w, h = (HEIGHT, WIDTH) if orientation == LANDSCAPE else (WIDTH, HEIGHT)
        bitmaps.append((rect_bitmap(w, h, rects), orientation))
    return build_note(bitmaps)


def main() -> None:
    two_page = build([
        # page 1: black square + gray band (marker-ish)
        ([(BLACK, 300, 400, 700, 800), (GRAY, 200, 1000, 1200, 1100)],
         PORTRAIT),
        # page 2: dark gray rectangle
        ([(DARK_GRAY, 100, 100, 400, 300)], PORTRAIT),
    ])
    landscape = build([
        ([(BLACK, 1000, 200, 1600, 600)], LANDSCAPE),
    ])
    (HERE / "synthetic-two-page.note").write_bytes(two_page)
    (HERE / "synthetic-landscape.note").write_bytes(landscape)
    print(f"synthetic-two-page.note: {len(two_page)} bytes")
    print(f"synthetic-landscape.note: {len(landscape)} bytes")


if __name__ == "__main__":
    main()
