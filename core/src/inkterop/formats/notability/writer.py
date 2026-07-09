"""IR -> Notability modern .ntb (zip + FlatBuffers noteBundle).

Exact inverse of ntb.py's reader: emits precisely the tables/slots its
``_Table`` accessors consume, and mirrors the committed fixture
(``core/tests/fixtures/notability/scribbles.ntb``, Mac app 16.5.3) for
every byte the reader does NOT consume — the ``[unknown]`` constants
below all carry the fixture-observed values (see
docs/formats/notability.md, ".ntb" section, for per-claim confidence).

Geometry: strokes are polylines in the IR, but .ntb stores fitted cubic
Bezier chains. We emit one *exactly linear* cubic per polyline segment
(control points at 1/3 and 2/3 of the chord), so the reader's uniform
flattening reproduces the written polyline verbatim — every read-back
sample lies on the written segments and every anchor round-trips as f32.
Per-anchor pressure profiles ride the f16 width-multiplier channel.

Multi-page: the op log has only been observed single-page-scoped (the
reader emits one continuous-scroll page); documents with more than one
page are written page 1 only, with a warning — honest lossage rather
than guessed framing.

Ships ``validated=False`` (docs/validated-writes.md): the color byte
order (R vs G — open question #4 in the format doc; a red corpus case is
pending) and the [unknown] constants are untested against the app.
RGBA is emitted exactly as ntb.py interprets it, so write->read
round-trips in-repo regardless of how that question resolves.
"""
from __future__ import annotations

import io
import json
import logging
import struct
import time
import uuid
import zipfile
from pathlib import Path
from statistics import median
from typing import Any

from ... import ir
from ..base import Fidelity
from .._scale import unit_factor
from .fb import FbBuilder
from .ntb import FORMAT_ID, OP_DOC_METADATA, OP_STROKE, TOOL_FAMILIES

_logger = logging.getLogger(__name__)

# Notability page coordinates are PDF points [inferred].
NTB_SCALE = 1.0

# Container constants mirrored from the fixture (Mac app 16.5.3).
VERSION_MEMBER = b"1"
MANIFEST_JSON = b'{\n  "appVersion" : "16.5.3"\n}'

# Type-1 metadata op constants observed in the fixture.
PAGE_MARGINS = (36.0, 36.0, 36.0, 36.0)  # [inferred] margins, pt
LOCALE = "en_US"
FONT_NAME, FONT_SIZE = "Inter", 14.0

# Inverse of ntb.TOOL_FAMILIES for foreign strokes; anything unmapped
# degrades to pen (0).
FAMILY_TOOL = {fam: tid for tid, fam in TOOL_FAMILIES.items()}
FAMILY_TOOL[ir.ToolFamily.MECHANICAL_PENCIL] = 1  # -> pencil

# Base widths observed in the fixture (pen/pencil 3.1875, highlighter
# 15.9375 pt) — the app's own defaults, used for fidelity=native and as
# a last-resort fallback.
DEFAULT_WIDTH = {0: 3.1875, 1: 3.1875, 2: 15.9375}
HIGHLIGHTER_ALPHA = 107 / 255  # fixture's yellow highlighter alpha

OP_UNKNOWN_3 = 3  # [unknown] op emitted by the app between metadata and ink


def _f16(v: float) -> float:
    """Clamp into half-float range (multipliers are ~0-10 in practice)."""
    return min(max(v, -65504.0), 65504.0)


def encode_point_blob(xs: list[float], ys: list[float],
                      mults: list[float]) -> bytes:
    """Origin-relative polyline -> the [verified] .ntb point-blob framing.

    coord_fmt 1 (f32 coords) always; one exactly-linear cubic segment per
    polyline segment. Inverse of ntb.decode_point_blob."""
    n = len(xs)
    if n > 0xFFFF:
        raise ValueError(f".ntb point blob caps at 65535 anchors (got {n})")
    out = bytearray(struct.pack("<BHB", 1, n, 3))  # fmt, count, [unknown] 3
    out += struct.pack("<II", 0, 0)  # [unknown] zeros (2nd is fmt-1 only)
    for i in range(n - 1):
        out += struct.pack("<eeBH", _f16(mults[i]), 1.0, 0xFF, 0)
        x0, y0, x1, y1 = xs[i], ys[i], xs[i + 1], ys[i + 1]
        out += struct.pack(
            "<6f",
            x0 + (x1 - x0) / 3.0, y0 + (y1 - y0) / 3.0,  # control 1
            x0 + 2.0 * (x1 - x0) / 3.0, y0 + 2.0 * (y1 - y0) / 3.0,  # control 2
            x1, y1,  # segment end
        )
    out += struct.pack("<eeBB", _f16(mults[-1]), 1.0, 0xFF, 0)  # 6B tail
    return bytes(out)


def _tool_id(s: ir.Stroke) -> int:
    native = s.tool.native if s.tool else None
    if (native and native.format_id == FORMAT_ID
            and isinstance(native.tool_id, int)):
        return native.tool_id  # .ntb round-trip (legacy reader uses "curve")
    return FAMILY_TOOL.get(s.tool.family if s.tool else None, 0)


def _base_and_mults(s: ir.Stroke, k: float, tool: int,
                    fidelity: Fidelity) -> tuple[float, list[float]]:
    """Base width (pt) + per-anchor multipliers (base * mult = rendered)."""
    n = len(s.x)
    if fidelity is Fidelity.NATIVE:
        return DEFAULT_WIDTH.get(tool, DEFAULT_WIDTH[0]), [1.0] * n

    widths = s.channels.get(ir.Channel.WIDTH)
    native = s.tool.native if s.tool else None
    base = 0.0
    if (native and native.format_id == FORMAT_ID
            and isinstance(native.tool_id, int)):
        base = float(native.params.get("width") or 0.0)  # exact round-trip
    if base <= 0.0 and s.appearance is not None and s.appearance.width:
        base = s.appearance.width * k
    if base <= 0.0 and widths:
        base = median(widths) * k
    if base <= 0.0:
        base = DEFAULT_WIDTH.get(tool, DEFAULT_WIDTH[0])
    if widths:
        return base, [w * k / base for w in widths[:n]] + \
            [1.0] * (n - len(widths))
    return base, [1.0] * n


def _rgba(s: ir.Stroke, fidelity: Fidelity, tool: int) -> bytes:
    """RGBA in the byte order the reader interprets (R-vs-G [inferred])."""
    if fidelity is not Fidelity.NATIVE and s.appearance is not None:
        color, alpha = s.appearance.color, s.appearance.opacity
    else:
        color = s.color
        native = s.tool.native if s.tool else None
        alpha = None
        if (fidelity is not Fidelity.NATIVE and native
                and native.format_id == FORMAT_ID):
            alpha = native.params.get("alpha")
        if alpha is None:
            alpha = HIGHLIGHTER_ALPHA if tool == 2 else 1.0
    to_byte = (lambda v: round(min(max(v, 0.0), 1.0) * 255))
    return bytes([to_byte(color.r), to_byte(color.g), to_byte(color.b),
                  to_byte(alpha)])


def _stroke_payload(fb: FbBuilder, s: ir.Stroke, k: float,
                    x0: float, y0: float, fidelity: Fidelity) -> int:
    tool = _tool_id(s)
    xs = [(x - x0) * k for x in s.x]
    ys = [(y - y0) * k for y in s.y]
    base, mults = _base_and_mults(s, k, tool, fidelity)
    blob = encode_point_blob([x - xs[0] for x in xs],
                             [y - ys[0] for y in ys], mults)
    slots = {
        0: ("struct", struct.pack("<3I", 0, 1, 0), 4),  # [unknown] page ref?
        1: ("f32s", (xs[0], ys[0])),  # origin = first anchor, page pt
        7: ("struct", _rgba(s, fidelity, tool), 4),
        8: ("f32", base),
        9: ("ref", fb.byte_vector(blob)),
        15: ("u32", len(s.x)),  # [unknown] raw input event count; stand-in
    }
    if tool:
        slots[4] = ("u8", tool)
        slots[5] = ("u8", 1)  # [unknown]; absent on the fixture's pen stroke
    if tool == 2:
        slots[14] = ("u32", 999999)  # [unknown] highlighter-only constant
    return fb.table(slots)


def _metadata_payload(fb: FbBuilder, title: str, w: float, h: float) -> int:
    style = fb.table({2: ("u8", 1), 3: ("u8", 0), 5: ("u8", 13)})  # [unknown]
    attrs = fb.table({
        0: ("ref", style),
        3: ("f32s", (w, h)),  # page size, pt
        4: ("f32s", PAGE_MARGINS),
    })
    return fb.table({
        0: ("ref", fb.table({0: ("ref", fb.string(title))})),
        1: ("ref", fb.table({0: ("ref", attrs)})),
        2: ("ref", fb.table({0: ("ref", fb.string(LOCALE))})),
        3: ("u8", 1),  # [unknown]
        4: ("ref", fb.string(FONT_NAME)),
        5: ("f32", FONT_SIZE),
        6: ("u8", 0),  # [unknown]
    })


def _op(fb: FbBuilder, seq: int, op_type: int, payload: int,
        ts: int, pen_up: int | None = None,
        pen_down: int | None = None) -> int:
    slots = {
        0: ("struct", struct.pack("<II", 0, seq), 4),  # (0, sequence)
        1: ("u64", ts),
        4: ("u8", op_type),
        5: ("ref", payload),
    }
    if pen_up is not None:
        slots[2] = ("u64", pen_up)
    if pen_down is not None:
        slots[3] = ("u64", pen_down)
    return fb.table(slots)


def document_to_note_bundle(doc: ir.Document,
                            fidelity: Fidelity = Fidelity.EXACT) -> bytes:
    if fidelity is Fidelity.RAW:
        raise ValueError(
            ".ntb stores rendered width profiles, not raw pen dynamics "
            "(pressure/tilt/speed); use .json (IR) or InkML"
        )
    if len(doc.pages) > 1:
        _logger.warning(
            ".ntb writer: the op log is single-page-scoped as observed "
            "(multi-page framing [unknown]); writing page 1 of %d and "
            "dropping the rest", len(doc.pages),
        )
    page = doc.pages[0] if doc.pages else ir.Page(
        bounds=ir.Rect(0.0, 0.0, 612.0, 792.0), point_scale=1.0)
    k = unit_factor(page, NTB_SCALE)
    b = page.bounds

    created = int(doc.metadata.get("created_unix_ms")
                  or time.time() * 1000)
    uid = str(doc.metadata.get("notability_uuid") or uuid.uuid4()).lower()

    fb = FbBuilder()
    ops = [
        _op(fb, 0, OP_DOC_METADATA,
            _metadata_payload(fb, doc.title or "inkterop export",
                              b.width * k, b.height * k),
            created, pen_up=created),
        # [unknown] op the app always emits before the first stroke.
        _op(fb, 1, OP_UNKNOWN_3, fb.table({2: ("u32", 2)}),
            created + 1, pen_up=created + 1),
    ]
    seq, t = 3, created + 1000  # stroke sequence numbers: odd, ascending
    for layer in page.layers:
        if not layer.visible:
            continue
        for s in layer.strokes:
            if not s.x:
                continue
            payload = _stroke_payload(fb, s, k, b.x_min, b.y_min, fidelity)
            # fixture-style timestamps: pen-down < op ts < pen-up
            ops.append(_op(fb, seq, OP_STROKE, payload,
                           t + 400, pen_up=t + 500, pen_down=t))
            seq += 2
            t += 1000

    root = fb.table({
        0: ("struct", bytes(16), 4),  # [unknown] opaque 16B (hash?); zeros
        3: ("ref", fb.string(uid.upper())),
        4: ("u64", created),
        5: ("ref", fb.string(uid)),
        6: ("ref", fb.vector_of_tables(ops)),
        7: ("u16", 12),  # [unknown] schema/protocol version; constant 12
    })
    return fb.finish(root)


def _thumbnail_png(w_pt: float, h_pt: float) -> bytes:
    """Plain white page-aspect placeholder (the app re-renders its own)."""
    from PIL import Image

    aspect = (h_pt / w_pt) if w_pt > 0 else 792.0 / 612.0
    size = (120, max(1, min(4000, round(120 * aspect))))
    buf = io.BytesIO()
    Image.new("RGB", size, (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


class NtbWriter:
    format_id = FORMAT_ID
    extensions = (".ntb",)
    # Pending an app-open check AND the color byte-order corpus case
    # (docs/formats/notability.md open question #4).
    validated = False

    def write(self, doc: ir.Document, path: Path, fidelity: Fidelity,
              options: dict[str, Any] | None = None) -> None:
        bundle = document_to_note_bundle(doc, fidelity)
        page = doc.pages[0] if doc.pages else None
        w = page.bounds.width * page.point_scale if page else 612.0
        h = page.bounds.height * page.point_scale if page else 792.0
        # Member list, order, and STORED compression mirror the fixture.
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("version", VERSION_MEMBER)
            zf.writestr("noteBundle", bundle)
            zf.writestr("manifest.json", MANIFEST_JSON)
            zf.writestr("thumbnail.png", _thumbnail_png(w, h))
