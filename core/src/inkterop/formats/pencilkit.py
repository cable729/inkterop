"""Apple PencilKit PKDrawing -> IR.

PKDrawing is the serialized stroke container behind `PKCanvasView`
(`PKDrawing.dataRepresentation()`): Apple Notes, Freeform, Screenshot
markup and any PencilKit-hosting app persist ink as these blobs, usually
embedded inside a database rather than as standalone files. `.pkdrawing`
is OUR chosen extension for a bare blob on disk; a future apple-notes
reader should extract blobs from the Notes store and call
:func:`parse_pkdrawing` on them directly.

Format facts are our own reverse engineering, validated point-exact
against a live PencilKit oracle (macOS 26, container version 1) across
22 self-generated corpus cases — see docs/formats/pencilkit.md and the
generator/decoder in corpus/scratch/ (pkgen.swift, pkdecode.py).

Container [verified]: magic ``wrd\\xf0`` + u16 LE version (=1), then one
plain protobuf message. Top-level: field 4 (repeated) = ink table
(RGBA f32 color + ink-id string), field 5 (repeated) = strokes in
z-order. Stroke: 5.4 ink-table index, 5.5 path, 5.6 renderBounds f32x4.
Path: creationDate (CFAbsoluteTime double), point count, complementary
channel bitmasks (per-point 5.5.4 / constant 5.5.5, union 0x7FF),
constant block 5.5.6, fixed-stride point array 5.5.7. Eleven channels,
ascending bit order (see _CHANNELS). CRDT/replica fields are ignored on
read [verified layout, inferred semantics].

The protobuf wire walker below is self-contained (same independent
subset as formats/goodnotes/wire.py).
"""
from __future__ import annotations

import math
import struct
from pathlib import Path

from .. import ir

FORMAT_ID = "pencilkit"
MAGIC = b"wrd\xf0"
CF_EPOCH_TO_UNIX = 978307200.0  # 2001-01-01 -> 1970-01-01, seconds
_TWO_PI = 2.0 * math.pi
_FULL_MASK = 0x7FF  # 11 channels


class PkDrawingError(ValueError):
    pass


# --- protobuf wire walking ---------------------------------------------------

def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(buf):
            raise PkDrawingError("varint past end of buffer")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise PkDrawingError("varint too long")


def _parse_message(buf: bytes) -> list[tuple[int, object]]:
    """Wire fields in order: varint -> int, fixed64 -> double,
    len-delimited -> bytes, fixed32 -> float."""
    fields: list[tuple[int, object]] = []
    pos = 0
    while pos < len(buf):
        key, pos = _read_varint(buf, pos)
        number, wtype = key >> 3, key & 7
        if number == 0:
            raise PkDrawingError("field number 0")
        value: object
        if wtype == 0:
            value, pos = _read_varint(buf, pos)
        elif wtype == 1:
            if pos + 8 > len(buf):
                raise PkDrawingError("truncated fixed64")
            value = struct.unpack_from("<d", buf, pos)[0]
            pos += 8
        elif wtype == 2:
            length, pos = _read_varint(buf, pos)
            if pos + length > len(buf):
                raise PkDrawingError("truncated bytes field")
            value = buf[pos:pos + length]
            pos += length
        elif wtype == 5:
            if pos + 4 > len(buf):
                raise PkDrawingError("truncated fixed32")
            value = struct.unpack_from("<f", buf, pos)[0]
            pos += 4
        else:
            raise PkDrawingError(f"unsupported wire type {wtype}")
        fields.append((number, value))
    return fields


def _group(buf: bytes) -> dict[int, list]:
    out: dict[int, list] = {}
    for number, value in _parse_message(buf):
        out.setdefault(number, []).append(value)
    return out


# --- channel table -----------------------------------------------------------
# bit in the per-point (5.5.4) / constant (5.5.5) masks -> struct fmt.
# Blocks concatenate the selected channels in ascending bit order, no
# padding; identical encoding in the constant block and per-point records.
_CHANNELS: tuple[tuple[int, str, str], ...] = (
    (0, "location", "ff"),       # x, y (points) [verified]
    (1, "timeOffset", "f"),      # seconds since path creationDate [verified]
    (2, "width", "f"),           # size.width [verified]
    (3, "aspect", "H"),          # size.height = width * v/1000 [verified]
    (4, "unknown4", "H"),        # always 0 in corpus [unknown]
    (5, "force", "H"),           # v/1000 [verified]
    (6, "azimuth", "H"),         # (2v/65535)*pi - pi, radians [verified]
    (7, "altitude", "H"),        # (1 - v/65535)*pi/2, radians [verified]
    (8, "opacity", "H"),         # 2v/65535 [verified]
    (9, "secondaryWidth", "f"),  # width * secondaryScale [verified]
    (10, "unknown10", "H"),      # always 0 in corpus [unknown]
)

_BIT_ASPECT = 3
_BIT_OPACITY = 8
_BIT_SECONDARY = 9


def _decode_value(name: str, raw: float) -> float:
    if name in ("force", "aspect"):
        return raw / 1000.0
    if name == "azimuth":
        return (2 * raw / 65535) * math.pi - math.pi
    if name == "altitude":
        return (1 - raw / 65535) * (math.pi / 2)
    if name == "opacity":
        return 2 * raw / 65535
    return raw  # f32 channels and unknown u16s pass through


def _read_block(buf: bytes, pos: int, mask: int) -> tuple[dict, int]:
    """One channel block (constant block or per-point record) -> dict."""
    out: dict[str, float] = {}
    for bit, name, fmt in _CHANNELS:
        if not (mask >> bit) & 1:
            continue
        size = struct.calcsize("<" + fmt)
        if pos + size > len(buf):
            raise PkDrawingError(f"channel block overrun at {name}")
        vals = struct.unpack_from("<" + fmt, buf, pos)
        pos += size
        if name == "location":
            out["x"], out["y"] = vals
        else:
            out[name] = _decode_value(name, vals[0])
    return out, pos


# --- message decoding --------------------------------------------------------

def _decode_path(buf: bytes) -> dict:
    g = _group(buf)
    try:
        count = g[3][0]
        ppmask = g[4][0]
        cmask = g[5][0]
    except KeyError as e:
        raise PkDrawingError(f"path missing field {e}") from e
    if ppmask | cmask != _FULL_MASK or ppmask & cmask:
        raise PkDrawingError(f"bad channel masks pp={ppmask:#x} const={cmask:#x}")
    const_raw = g[6][0] if 6 in g else b""
    pts_raw = g[7][0] if 7 in g else b""
    const, used = _read_block(const_raw, 0, cmask)
    if used != len(const_raw):
        raise PkDrawingError(f"constant block residual: {used}/{len(const_raw)}")
    points, pos = [], 0
    for _ in range(count):
        pt, pos = _read_block(pts_raw, pos, ppmask)
        pt.update(const)
        points.append(pt)
    if pos != len(pts_raw):
        raise PkDrawingError(f"point array residual: {pos}/{len(pts_raw)}")
    return {
        "created_cf": g[2][0] if 2 in g else None,
        "uuid": g[1][0].hex() if 1 in g else None,
        "ppmask": ppmask,
        "points": points,
    }


def _decode_ink(buf: bytes) -> dict:
    g = _group(buf)
    rgba = [v for _, v in _parse_message(g[1][0])] if 1 in g else []
    if len(rgba) != 4:
        raise PkDrawingError(f"ink color has {len(rgba)} components")
    ink_id = g[2][0].decode() if 2 in g else ""
    return {"ink": ink_id, "rgba": tuple(float(c) for c in rgba)}


# --- IR mapping --------------------------------------------------------------

#: PencilKit ink identifier -> neutral tool family (lower-cased lookup).
#: pen/pencil/marker [verified in corpus]; the rest [inferred] from the
#: PKInkType cases documented by Apple, not yet seen serialized.
INK_FAMILY = {
    "com.apple.ink.pen": ir.ToolFamily.PEN,
    "com.apple.ink.pencil": ir.ToolFamily.PENCIL,
    "com.apple.ink.marker": ir.ToolFamily.MARKER,
    "com.apple.ink.monoline": ir.ToolFamily.FINELINER,
    "com.apple.ink.fountainpen": ir.ToolFamily.CALLIGRAPHY,
    "com.apple.ink.watercolor": ir.ToolFamily.BRUSH,
    "com.apple.ink.crayon": ir.ToolFamily.PENCIL,
}

_FALLBACK_ALTITUDE = math.pi / 2


def _ir_stroke(buf: bytes, inks: list[dict]) -> tuple[ir.Stroke, tuple] | None:
    g = _group(buf)
    ink_index = g[4][0] if 4 in g else 0
    if not 0 <= ink_index < len(inks):
        raise PkDrawingError(f"stroke references ink {ink_index} "
                             f"of {len(inks)}")
    ink = inks[ink_index]
    if 5 not in g:
        raise PkDrawingError("stroke has no path")
    path = _decode_path(g[5][0])
    pts = path["points"]
    if not pts:
        return None
    ppmask = path["ppmask"]

    render_bounds = None
    if 6 in g:
        rb = [v for _, v in _parse_message(g[6][0])]
        if len(rb) == 4:
            render_bounds = tuple(float(v) for v in rb)

    widths = [p.get("width", 0.0) for p in pts]
    extra_pk: dict = {}
    if path["uuid"]:
        extra_pk["path_uuid"] = path["uuid"]
    if path["created_cf"] is not None:
        extra_pk["created_unix"] = path["created_cf"] + CF_EPOCH_TO_UNIX
    if render_bounds is not None:
        extra_pk["render_bounds"] = list(render_bounds)

    channels: dict[ir.Channel, list[float]] = {
        ir.Channel.WIDTH: widths,
        ir.Channel.TIMESTAMP: [p.get("timeOffset", 0.0) for p in pts],
        # PencilKit azimuth is -pi..pi from +x; other readers emit 0..2pi,
        # so normalize (same angle, contract keeps 0 = +x axis).
        ir.Channel.TILT_AZIMUTH:
            [p.get("azimuth", 0.0) % _TWO_PI for p in pts],
        ir.Channel.TILT_ALTITUDE:
            [p.get("altitude", _FALLBACK_ALTITUDE) for p in pts],
    }
    # UITouch force can exceed 1.0 (Pencil max ~4.17); the IR PRESSURE
    # contract is 0-1, so clamp and stash the raw values when it matters.
    force = [p.get("force", 0.0) for p in pts]
    clamped = [min(max(f, 0.0), 1.0) for f in force]
    if clamped != force:
        extra_pk["force_raw"] = force
    channels[ir.Channel.PRESSURE] = clamped

    if (ppmask >> _BIT_OPACITY) & 1:
        channels[ir.Channel.ALPHA] = [p.get("opacity", 1.0) for p in pts]
        opacity = 1.0  # per-point ALPHA wins (style.py contract)
    else:
        opacity = pts[0].get("opacity", 1.0)

    # PencilKit-only geometry: nib aspect (height = width * aspect) and
    # secondary width. Constants ride in NativeTool params; per-point
    # series go to extra (no IR channel for them).
    params: dict = {}
    aspects = [p.get("aspect", 1.0) for p in pts]
    if (ppmask >> _BIT_ASPECT) & 1:
        extra_pk["aspect"] = aspects
    else:
        params["aspect"] = aspects[0]
    sec_scales = [
        (p.get("secondaryWidth", w) / w) if w else 1.0
        for p, w in zip(pts, widths)
    ]
    if (ppmask >> _BIT_SECONDARY) & 1:
        extra_pk["secondary_scale"] = sec_scales
    else:
        params["secondary_scale"] = sec_scales[0]
    for unk in ("unknown4", "unknown10"):
        vals = [p.get(unk, 0) for p in pts]
        if any(vals):  # always 0 in corpus; preserve if that ever changes
            extra_pk[unk] = vals

    color = ir.Color(*ink["rgba"])
    family = INK_FAMILY.get(ink["ink"].lower(), ir.ToolFamily.UNKNOWN)
    stroke = ir.Stroke(
        x=[p["x"] for p in pts],
        y=[p["y"] for p in pts],
        tool=ir.ToolRef(
            family=family,
            native=ir.NativeTool(FORMAT_ID, ink["ink"], params),
        ),
        color=color,
        channels=channels,
        # PencilKit renders every ink with per-point widths; marker is a
        # translucent wide nib but composites normally [inferred].
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_VARIABLE,
            color=color,
            width=None,
            opacity=opacity,
            blend=ir.BlendMode.NORMAL,
            cap=ir.LineCap.ROUND,
            underlay=False,
        ),
        extra={FORMAT_ID: extra_pk},
    )
    return stroke, render_bounds


_BOUNDS_PAD = 10.0
_DEFAULT_BOUNDS = ir.Rect(0.0, 0.0, 612.0, 792.0)  # empty drawing: letter


def parse_pkdrawing(data: bytes, title: str = "") -> ir.Document:
    """Decode a raw PKDrawing blob (`PKDrawing.dataRepresentation()`).

    Entry point for embedders too: an apple-notes reader can call this on
    blobs pulled out of the Notes store.
    """
    if data[:4] != MAGIC:
        raise PkDrawingError("not a PKDrawing blob (bad magic)")
    if len(data) < 6:
        raise PkDrawingError("truncated PKDrawing header")
    version = struct.unpack_from("<H", data, 4)[0]
    top = _group(data[6:])
    inks = [_decode_ink(b) for b in top.get(4, [])]

    strokes: list[ir.Stroke] = []
    rects: list[tuple] = []
    for blob in top.get(5, []):
        decoded = _ir_stroke(blob, inks)
        if decoded is None:
            continue
        stroke, rb = decoded
        strokes.append(stroke)
        if rb is not None:
            rects.append(rb)
        else:  # fall back to the point extent
            rects.append((min(stroke.x), min(stroke.y),
                          max(stroke.x) - min(stroke.x),
                          max(stroke.y) - min(stroke.y)))

    # PKDrawing has no page: content bbox = union of stroke renderBounds,
    # padded. Coordinates are typographic points at 1x [inferred] ->
    # point_scale 1.0.
    if rects:
        bounds = ir.Rect(
            min(r[0] for r in rects) - _BOUNDS_PAD,
            min(r[1] for r in rects) - _BOUNDS_PAD,
            max(r[0] + r[2] for r in rects) + _BOUNDS_PAD,
            max(r[1] + r[3] for r in rects) + _BOUNDS_PAD,
        )
    else:
        bounds = _DEFAULT_BOUNDS

    page = ir.Page(bounds=bounds, point_scale=1.0,
                   layers=[ir.Layer(strokes=strokes)])
    return ir.Document(
        format_id=FORMAT_ID,
        title=title,
        pages=[page],
        metadata={"pk_container_version": version,
                  "ink_ids": [i["ink"] for i in inks]},
    )


class PkDrawingReader:
    format_id = FORMAT_ID
    extensions = (".pkdrawing",)

    def detect(self, path: Path) -> bool:
        try:
            with open(path, "rb") as f:
                head = f.read(6)
        except OSError:
            return False
        return len(head) == 6 and head[:4] == MAGIC

    def read(self, path: Path) -> ir.Document:
        return parse_pkdrawing(path.read_bytes(), title=path.stem)
