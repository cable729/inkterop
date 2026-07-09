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
from pathlib import Path
from statistics import median
from typing import Any

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
    """Pack ARGB as the SIGNED int32 Saber (Dart BSON) stores."""
    v = ((round(max(0.0, min(1.0, opacity)) * 255) << 24)
         | (round(color.r * 255) << 16)
         | (round(color.g * 255) << 8)
         | round(color.b * 255))
    return v - 2 ** 32 if v >= 2 ** 31 else v


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

    if native:
        tool_name = native.tool_id
        size = float(native.params.get("size") or 2.0)
        smoothing = native.params.get("smoothing")
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

    return {
        "shape": None,
        "p": blobs,
        "i": page_index,
        "ty": tool_name,
        "pe": pe,
        "c": _argb_int(color, opacity),
        "s": float(size),
        "sm": float(smoothing) if smoothing is not None else 0.5,
        "sp": False,
    }


def document_to_sbn2(doc: ir.Document,
                     fidelity: Fidelity = Fidelity.EXACT) -> bytes:
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
        pages.append({
            "w": b.width * k,
            "h": b.height * k,
            "s": strokes,
            "q": quill,
        })
    return encode_bson({
        "v": 19, "ni": 0, "b": None, "p": "", "l": 40, "lt": 3,
        "z": pages, "c": 0,
    })


class SaberWriter:
    format_id = FORMAT_ID
    extensions = (".sba", ".sbn2")
    validated = False  # pending Saber Mac app-open check

    def write(self, doc: ir.Document, path: Path, fidelity: Fidelity,
              options: dict[str, Any] | None = None) -> None:
        data = document_to_sbn2(doc, fidelity)
        if path.suffix.lower() == ".sba":
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("main.sbn2", data)
        else:
            path.write_bytes(data)
