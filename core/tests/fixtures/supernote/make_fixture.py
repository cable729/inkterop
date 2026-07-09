"""Generate the synthetic Supernote X-series .note fixtures in this dir.

The container layout is reimplemented from supernotelib's parser
(jya-dev/supernote-tool, Apache-2.0): a 4-byte file type ("note"), an
ASCII signature "SN_FILE_VER_YYYYNNNN" at offset 4, then length-prefixed
blocks (4-byte little-endian length + payload). Metadata blocks are
`<KEY:VALUE>` strings; a footer block maps names to block addresses and
the file's last 4 bytes hold the footer address. Layer bitmaps use the
RATTA_RLE protocol: (colorcode, length) byte pairs.

Run: uv run python tests/fixtures/supernote/make_fixture.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent

SIGNATURE = b"SN_FILE_VER_20220011"  # firmware Chauvet 2.5.17 era
WIDTH, HEIGHT = 1404, 1872  # A5X/A6X2 portrait screen pixels

# RATTA_RLE color codes (X-series)
BG = 0x62
BLACK = 0x61
DARK_GRAY = 0x63
GRAY = 0x64

PORTRAIT, LANDSCAPE = "1000", "1090"  # page ORIENTATION values


def meta(params: dict) -> bytes:
    return "".join(f"<{k}:{v}>" for k, v in params.items()).encode()


def rle(width: int, height: int, rects) -> bytes:
    """RATTA_RLE-encode background + non-overlapping solid rects.

    rects: (colorcode, x0, y0, x1, y1) with exclusive x1/y1.
    """
    runs: list[list[int]] = []

    def emit(code: int, n: int) -> None:
        if n <= 0:
            return
        if runs and runs[-1][0] == code:
            runs[-1][1] += n
        else:
            runs.append([code, n])

    for y in range(height):
        x = 0
        for code, x0, _y0, x1, _y1 in sorted(
            (r for r in rects if r[2] <= y < r[4]), key=lambda r: r[1]
        ):
            emit(BG, x0 - x)
            emit(code, x1 - x0)
            x = x1
        emit(BG, width - x)

    out = bytearray()
    for code, n in runs:
        while n >= 0x4000:  # length byte 0xff => special length 0x4000
            out += bytes((code, 0xFF))
            n -= 0x4000
        while n > 0x80:  # plain length byte L (high bit clear) => L + 1
            out += bytes((code, 0x7F))
            n -= 0x80
        if n:
            out += bytes((code, n - 1))
    return bytes(out)


class Builder:
    def __init__(self) -> None:
        self.buf = bytearray()

    def raw(self, data: bytes) -> int:
        addr = len(self.buf)
        self.buf += data
        return addr

    def block(self, data: bytes) -> int:
        addr = len(self.buf)
        self.buf += len(data).to_bytes(4, "little") + data
        return addr


def _layer_info() -> str:
    """LAYERINFO visibility JSON; the device stores ':' as '#'."""
    info = json.dumps(
        [
            {"layerId": 0, "name": "Layer 1", "isBackgroundLayer": False,
             "isVisible": True, "isDeleted": False, "isCurrentLayer": True},
            {"layerId": 99, "name": "Background Layer",
             "isBackgroundLayer": True, "isVisible": True,
             "isDeleted": False, "isCurrentLayer": False},
        ],
        separators=(",", ":"),
    )
    return info.replace(":", "#")


def build_note(pages: list[tuple[list, str]]) -> bytes:
    """pages: list of (rects, orientation) tuples."""
    b = Builder()
    b.raw(b"note")
    b.raw(SIGNATURE)
    header_addr = b.block(meta({
        "MODULE_LABEL": "SNFILE_FEATURE",
        "FILE_TYPE": "NOTE",
        "APPLY_EQUIPMENT": "N2",
        "FINALOPERATION_PAGE": "1",
        "FINALOPERATION_LAYER": "1",
        "DEVICE_DPI": "0",
        "SOFT_DPI": "0",
        "FILE_PARSE_TYPE": "0",
        "RATTA_ETMD": "0",
        "APP_VERSION": "0",
        "FILE_ID": "F20260709000000000000000000000001",
        "FILE_RECOGN_TYPE": "0",
    }))
    footer: dict = {"FILE_FEATURE": header_addr}
    for i, (rects, orientation) in enumerate(pages, start=1):
        w, h = (HEIGHT, WIDTH) if orientation == LANDSCAPE else (WIDTH, HEIGHT)
        bitmap_addr = b.block(rle(w, h, rects))
        main_addr = b.block(meta({
            "LAYERTYPE": "NOTE",
            "LAYERPROTOCOL": "RATTA_RLE",
            "LAYERNAME": "MAINLAYER",
            "LAYERPATH": "0",
            "LAYERBITMAP": str(bitmap_addr),
            "LAYERVECTORGRAPH": "0",
            "LAYERRECOGN": "0",
        }))
        footer[f"PAGE{i}"] = b.block(meta({
            "PAGESTYLE": "style_white",
            "PAGESTYLEMD5": "0",
            "LAYERINFO": _layer_info(),
            "LAYERSEQ": "MAINLAYER,BGLAYER",
            "MAINLAYER": str(main_addr),
            "LAYER1": "0",
            "LAYER2": "0",
            "LAYER3": "0",
            "BGLAYER": "0",
            "ORIENTATION": orientation,
            "RECOGNSTATUS": "0",
            "RECOGNTEXT": "0",
            "RECOGNFILE": "0",
            "RECOGNFILESTATUS": "0",
            "TOTALPATH": "0",
            "PAGEID": f"P2026070900000000000000000000000{i}",
        }))
    footer["COVER_0"] = 0
    footer_addr = b.block(meta(footer))
    b.raw(b"tail")
    b.raw(footer_addr.to_bytes(4, "little"))
    return bytes(b.buf)


def main() -> None:
    two_page = build_note([
        # page 1: black square + gray band (marker-ish)
        ([(BLACK, 300, 400, 700, 800), (GRAY, 200, 1000, 1200, 1100)],
         PORTRAIT),
        # page 2: dark gray rectangle
        ([(DARK_GRAY, 100, 100, 400, 300)], PORTRAIT),
    ])
    landscape = build_note([
        ([(BLACK, 1000, 200, 1600, 600)], LANDSCAPE),
    ])
    (HERE / "synthetic-two-page.note").write_bytes(two_page)
    (HERE / "synthetic-landscape.note").write_bytes(landscape)
    print(f"synthetic-two-page.note: {len(two_page)} bytes")
    print(f"synthetic-landscape.note: {len(landscape)} bytes")


if __name__ == "__main__":
    main()
