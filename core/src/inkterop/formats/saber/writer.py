"""IR -> Saber .sba/.sbn2.

Exact inverse of reader.py's mapping, emitting the constants observed in
the self-made v19 fixture ([verified] top-level keys: v/ni/b/p/l/lt/z/c;
per-stroke: shape/p/i/ty/pe/c/s/sm/sp). Saber's GPL-3.0 pressure->width
curve is deliberately not reimplemented, so written strokes carry base
size + raw pressure and the app recomputes rendered width — `raw`/`native`
fidelity are faithful, `exact` is approximate by design.

RAW fidelity is accepted (pressure is the one raw channel Saber stores);
speed/tilt channels are dropped silently.

Ships validated=False until the Saber Mac app-open check passes
(docs/validated-writes.md).
"""
from __future__ import annotations

import struct
import zipfile
from io import BytesIO
from pathlib import Path
from statistics import median
from typing import Any

from PIL import Image

from ... import ir
from ..base import Fidelity
from .._scale import unit_factor
from .reader import FORMAT_ID, BsonError

# Saber canvas units -> points; a 1000u-wide page exports at 595pt
# [inferred], see reader.py.
SABER_SCALE = 595.0 / 1000.0

# Inverse of reader.TOOL_FAMILY, using the name casing Saber itself writes.
FAMILY_TOOL = {
    ir.ToolFamily.PEN: "fountainPen",
    ir.ToolFamily.BALLPOINT: "ballpoint",
    ir.ToolFamily.FINELINER: "fineliner",
    ir.ToolFamily.BRUSH: "brush",
    ir.ToolFamily.SHADER: "brush",
    ir.ToolFamily.CALLIGRAPHY: "fountainPen",
    ir.ToolFamily.MARKER: "fountainPen",
    ir.ToolFamily.PENCIL: "Pencil",
    ir.ToolFamily.MECHANICAL_PENCIL: "Pencil",
    ir.ToolFamily.HIGHLIGHTER: "Highlighter",
}


def encode_bson(doc: dict) -> bytes:
    """Minimal BSON encoder for exactly the subset parse_bson reads."""
    body = b"".join(_element(k, v) for k, v in doc.items())
    return struct.pack("<i", len(body) + 5) + body + b"\x00"


def _element(name: str, v: Any) -> bytes:
    key = name.encode() + b"\x00"
    if isinstance(v, bool):  # before int — bool subclasses int
        return b"\x08" + key + (b"\x01" if v else b"\x00")
    if v is None:
        return b"\x0a" + key
    if isinstance(v, float):
        return b"\x01" + key + struct.pack("<d", v)
    if isinstance(v, int):
        if -(2 ** 31) <= v < 2 ** 31:
            return b"\x10" + key + struct.pack("<i", v)
        return b"\x12" + key + struct.pack("<q", v)
    if isinstance(v, str):
        raw = v.encode()
        return b"\x02" + key + struct.pack("<i", len(raw) + 1) + raw + b"\x00"
    if isinstance(v, bytes):
        return b"\x05" + key + struct.pack("<i", len(v)) + b"\x00" + v
    if isinstance(v, dict):
        return b"\x03" + key + encode_bson(v)
    if isinstance(v, (list, tuple)):
        return b"\x04" + key + encode_bson({str(i): x for i, x in enumerate(v)})
    raise BsonError(f"cannot BSON-encode {type(v).__name__} for {name!r}")


def _argb_int(color: ir.Color, opacity: float) -> int:
    """Pack ARGB as the UNSIGNED int Saber (Dart) stores.

    Verified against the Mac fixture: 0xFF000000 black is written as
    int64 4278190080, not int32 -16777216 — Dart's BSON encoder keeps
    the unsigned value and widens to int64 when it exceeds int32 range
    (our encoder does the same). The first app-open check failed with
    the signed packing.
    """
    return ((round(max(0.0, min(1.0, opacity)) * 255) << 24)
            | (round(color.r * 255) << 16)
            | (round(color.g * 255) << 8)
            | round(color.b * 255))


def _stroke_doc(s: ir.Stroke, k: float, x0: float, y0: float,
                page_index: int) -> dict:
    native = s.tool.native if (s.tool and s.tool.native
                               and s.tool.native.format_id == FORMAT_ID) else None
    pressures = s.channels.get(ir.Channel.PRESSURE)
    pe = (bool(native.params.get("pressure_enabled")) if native
          else bool(pressures))

    blobs = []
    for i, (x, y) in enumerate(zip(s.x, s.y)):
        xx, yy = (x - x0) * k, (y - y0) * k
        if pe:
            p = pressures[i] if pressures and i < len(pressures) else 0.5
            blobs.append(struct.pack("<3f", xx, yy, p))
        else:
            blobs.append(struct.pack("<2f", xx, yy))

    raw: dict = {}
    if native:
        tool_name = native.tool_id
        size = float(native.params.get("size") or 2.0)
        smoothing = native.params.get("smoothing")
        raw = dict(native.params.get("raw") or {})
    else:
        tool_name = FAMILY_TOOL.get(s.tool.family if s.tool else None,
                                    "fountainPen")
        widths = s.channels.get(ir.Channel.WIDTH)
        # appearance.width is None for variable-width strokes
        # (STROKED_VARIABLE, e.g. reMarkable): width lives in the channel.
        if s.appearance is not None and s.appearance.width is not None:
            size = s.appearance.width * k
        elif widths:
            size = median(widths) * k
        else:
            size = 2.0
        smoothing = None

    if s.appearance is not None:
        color, opacity = s.appearance.color, s.appearance.opacity
    else:
        is_hl = s.tool is not None and s.tool.family is ir.ToolFamily.HIGHLIGHTER
        color, opacity = s.color, (0.5 if is_hl else 1.0)
    alphas = s.channels.get(ir.Channel.ALPHA)
    if alphas:
        # per-point alpha (e.g. reMarkable pencil texture) flattens to
        # its median — Saber has one ARGB per stroke
        opacity = median(alphas)

    out = {
        "shape": None,
        "p": blobs,
        "i": page_index,
        "ty": tool_name,
        "pe": pe,
        "c": _argb_int(color, opacity),
        "s": float(size),
        "sm": float(smoothing) if smoothing is not None else 0.5,
    }
    if raw:
        # replay tool-option keys the reader captured verbatim, in their
        # observed position (after sm, before sp), skipping the ones the
        # writer recomputes above
        for k, v in raw.items():
            if k not in out and k != "sp":
                out[k] = v
    elif tool_name == "Pencil":
        # observed app defaults for Pencil tool options — the loader may
        # require them [inferred from the Mac fixture]
        out.update({"sl": 0.1, "ts": 1.0, "te": 1.0})
    out["sp"] = bool(raw.get("sp", False))
    return out


def document_to_sbn2(doc: ir.Document,
                     fidelity: Fidelity = Fidelity.EXACT,
                     *, assets: list[bytes] | None = None) -> bytes:
    # .sba stores main.sbn2.N assets. A standalone .sbn2 embeds the same
    # bytes in the backwards-compatible top-level `a` array for portability.
    inline = assets is None
    if assets is None:
        assets = []
    asset_indices = {data: i for i, data in enumerate(assets)}
    next_image_id = 0

    def image_record(raster: ir.RasterImage, page: ir.Page, idx: int) -> dict:
        nonlocal next_image_id
        data = raster.data
        with Image.open(BytesIO(data)) as img:
            width, height = img.size
            ext = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}.get(img.format)
            if ext is None:
                buf = BytesIO()
                img.save(buf, format="PNG")
                data, ext = buf.getvalue(), ".png"
        if data not in asset_indices:
            asset_indices[data] = len(assets)
            assets.append(data)
        b = raster.bounds or page.bounds
        k = unit_factor(page, SABER_SCALE)
        record = {
            "id": next_image_id, "a": asset_indices[data], "e": ext,
            "i": idx, "v": False, "f": 1,
            "x": (b.x_min - page.bounds.x_min) * k,
            "y": (b.y_min - page.bounds.y_min) * k,
            "w": b.width * k, "h": b.height * k,
            "sw": float(width), "sh": float(height),
            "nw": float(width), "nh": float(height),
        }
        next_image_id += 1
        return record

    pages = []
    for idx, page in enumerate(doc.pages):
        k = unit_factor(page, SABER_SCALE)
        b = page.bounds
        strokes = [
            _stroke_doc(s, k, b.x_min, b.y_min, idx)
            for layer in page.layers if layer.visible
            for s in layer.strokes if len(s.x) >= 1
        ]
        quill = [{"insert": t.text}
                 for layer in page.layers if layer.visible
                 for t in layer.texts if t.text]
        if quill and not quill[-1]["insert"].endswith("\n"):
            quill[-1] = {"insert": quill[-1]["insert"] + "\n"}  # Quill docs end with \n
        page_doc: dict = {"w": b.width * k, "h": b.height * k}
        # the app omits empty s/q keys entirely (fixture page 2 is {w,h})
        if strokes:
            page_doc["s"] = strokes
        if quill:
            page_doc["q"] = quill
        raster_images = [image_record(layer.raster, page, idx)
                         for layer in page.layers if layer.visible and layer.raster is not None]
        if raster_images:
            page_doc["i"] = raster_images
        if isinstance(page.background, ir.ImageBackground):
            page_doc["b"] = image_record(page.background.image, page, idx)
        pages.append(page_doc)
    result = {
        "v": 19, "ni": next_image_id, "b": None, "p": "", "l": 40, "lt": 3,
        "z": pages, "c": 0,
    }
    if inline and assets:
        result["a"] = assets
    return encode_bson(result)


class SaberWriter:
    format_id = FORMAT_ID
    extensions = (".sba", ".sbn2")
    validated = False  # pending Saber Mac app-open check

    def write(self, doc: ir.Document, path: Path, fidelity: Fidelity,
              options: dict[str, Any] | None = None) -> None:
        if path.suffix.lower() == ".sba":
            assets: list[bytes] = []
            data = document_to_sbn2(doc, fidelity, assets=assets)
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("main.sbn2", data)
                for i, asset in enumerate(assets):
                    zf.writestr(f"main.sbn2.{i}", asset)
        else:
            path.write_bytes(document_to_sbn2(doc, fidelity))
