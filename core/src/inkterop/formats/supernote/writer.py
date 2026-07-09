"""IR -> Supernote X-series .note (raster MAINLAYER).

Container layout reimplemented from supernotelib's parser (jya-dev/
supernote-tool, Apache-2.0) — the same builder the synthetic fixtures
use ([verified]: supernotelib 0.7.1 parses these files; a real device
has NOT opened one yet, hence validated=False). Signature
SN_FILE_VER_20220011, length-prefixed blocks, <KEY:VALUE> metadata,
RATTA_RLE bitmaps, footer + trailing footer address.

This writer is raster-first like the reader: IR strokes are drawn with
Pillow onto the 1404x1872 device canvas (fit-contain, centered) and
quantized to the four X-series RLE gray codes. Supernote's per-stroke
TOTALPATH vector encoding remains undecoded, so NO vector data is
written — output is terminal for ink editability (the device can draw
on top, but existing ink is bitmap). NativeTool does not round-trip.

Layers carrying `Layer.raster` (supernote -> supernote round-trips)
are composited from their PNG instead of stroke drawing. All fidelities
collapse to raster; RAW raises (nothing raw survives a bitmap).
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from ... import ir
from ..base import Fidelity
from ...render.primitives import stroke_runs

FORMAT_ID = "supernote"

SIGNATURE = b"SN_FILE_VER_20220011"
WIDTH, HEIGHT = 1404, 1872  # portrait device pixels

BG = 0x62
BLACK = 0x61
DARK_GRAY = 0x63
GRAY = 0x64

PORTRAIT, LANDSCAPE = "1000", "1090"

# luminance -> RLE code thresholds (light -> dark)
def _code_for_lum(lum: int) -> int:
    if lum < 64:
        return BLACK
    if lum < 140:
        return DARK_GRAY
    if lum < 217:
        return GRAY
    return BG


def meta(params: dict) -> bytes:
    return "".join(f"<{k}:{v}>" for k, v in params.items()).encode()


def rle_encode(codes: bytes) -> bytes:
    """RATTA_RLE-encode a row-major code array (one byte per pixel)."""
    out = bytearray()

    def emit(code: int, n: int) -> None:
        while n >= 0x4000:  # length byte 0xff => special length 0x4000
            out.extend((code, 0xFF))
            n -= 0x4000
        while n > 0x80:  # plain length byte L (high bit clear) => L + 1
            out.extend((code, 0x7F))
            n -= 0x80
        if n:
            out.extend((code, n - 1))

    i, total = 0, len(codes)
    while i < total:
        code = codes[i]
        j = i + 1
        while j < total and codes[j] == code:
            j += 1
        emit(code, j - i)
        i = j
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


def build_note(page_bitmaps: list[tuple[bytes, str]]) -> bytes:
    """Assemble the container from (rle_bitmap, orientation) pages."""
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
    for i, (bitmap, orientation) in enumerate(page_bitmaps, start=1):
        bitmap_addr = b.block(bitmap)
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


def _lum(color: ir.Color, alpha: float) -> int:
    """Perceived luminance over a white page, 0-255."""
    lum = 0.299 * color.r + 0.587 * color.g + 0.114 * color.b
    return round((lum * alpha + (1.0 - alpha)) * 255)


def _draw_run(draw: ImageDraw.ImageDraw, pts: list[tuple[float, float]],
              width_px: int, fill: int) -> None:
    if len(pts) == 1:
        x, y = pts[0]
        r = max(width_px / 2.0, 0.5)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=fill)
        return
    draw.line(pts, fill=fill, width=max(width_px, 1), joint="curve")
    r = width_px / 2.0
    for x, y in (pts[0], pts[-1]):  # round caps
        draw.ellipse([x - r, y - r, x + r, y + r], fill=fill)


def render_page(page: ir.Page) -> tuple[bytes, str]:
    """Rasterize one IR page -> (RATTA_RLE bitmap, orientation)."""
    b = page.bounds
    landscape = b.width > b.height
    w, h = (HEIGHT, WIDTH) if landscape else (WIDTH, HEIGHT)
    img = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(img)

    k = min(w / b.width, h / b.height) if b.width and b.height else 1.0
    ox = (w - b.width * k) / 2.0
    oy = (h - b.height * k) / 2.0

    def to_px(x: float, y: float) -> tuple[float, float]:
        return ((x - b.x_min) * k + ox, (y - b.y_min) * k + oy)

    for layer in page.layers:
        if not layer.visible:
            continue
        if layer.raster is not None:
            src = Image.open(io.BytesIO(layer.raster.data)).convert("L")
            src = src.resize((round(b.width * k) or 1, round(b.height * k) or 1))
            img.paste(src, (round(ox), round(oy)))
            continue
        ordered = sorted(
            layer.strokes,
            key=lambda s: not (s.appearance is not None and s.appearance.underlay),
        )
        for s in ordered:
            for run in stroke_runs(s):
                fill = _lum(ir.Color(*run.rgb), run.alpha)
                if fill >= 245:  # invisible on white
                    continue
                pts = [to_px(x, y) for x, y in run.points]
                _draw_run(draw, pts, round(run.width * k), fill)

    codes = bytes(_code_for_lum(v) for v in img.tobytes())
    return rle_encode(codes), (LANDSCAPE if landscape else PORTRAIT)


class SupernoteWriter:
    format_id = FORMAT_ID
    extensions = (".note",)
    validated = False  # supernotelib round-trips; no real device check yet

    def write(self, doc: ir.Document, path: Path, fidelity: Fidelity,
              options: dict[str, Any] | None = None) -> None:
        if fidelity is Fidelity.RAW:
            raise ValueError(
                "supernote output is raster; raw pen dynamics cannot "
                "survive — use .json (IR) or InkML"
            )
        pages = [render_page(p) for p in doc.pages]
        path.write_bytes(build_note(pages))
