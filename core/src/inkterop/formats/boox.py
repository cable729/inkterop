"""Onyx Boox Notes (.note) -> IR.

Container ([verified] against boox-note-optimizer's Note Air 5c samples;
format facts cross-checked with the MIT-licensed
github.com/nrontsis/boox-note-optimizer docs + our own probing):

`.note` = zip. Single-note archives root everything at `<noteId>/`:

  <noteId>/note/pb/note_info          protobuf: doc metadata, page order,
                                      layer lists, canvas size
  <noteId>/pageModel/pb/<uuid>        protobuf: per-page dims + layers
  <noteId>/point/<pageId>/<pageId>#<pointsDocId>#points
                                      binary stroke points (see below)
  <noteId>/shape/<pageId>#<shapeDocId>#<ts>.zip
                                      nested zip -> protobuf: per-stroke
                                      style (pen type, color, thickness,
                                      transform, layer)
  <noteId>/stash/...                  undo history (ignored)

Multi-note archives add a root `note_tree` protobuf wrapping the same
per-note metadata messages [inferred, untested — no sample].

`#points` blob (all integers big-endian) [verified]:
  76B header: u32 (always 1 observed) + 36B ascii pageId (condensed,
  space-padded) + 36B ascii pointsDocId (hyphenated).
  Per stroke: 4B zero pad + N x 16B records `>ffBBHI` =
  (x, y, tilt_x, tilt_y, pressure 0-4095, t ms since stroke start).
  Trailing index: 44B entries (36B ascii shapeUUID + u32 offset + u32
  size), last 4B of blob = u32 index start offset.
  x/y are already PDF points (page 1860x2480 on Note Air 5c); the
  timestamps are cumulative, not deltas [verified — monotonic in all
  sample strokes].

Shape protobuf (nested zip member, repeated field 1 submessages)
[verified fields]: 1 shapeUUID, 2/3 created/modified epoch-ms, 4 ARGB
color (sign-extended varint), 5 thickness f32, 6 layer id, 7 bbox JSON,
8 3x3 affine JSON, 9 text style JSON, 10 plain text, 11 pen config JSON,
12 pen type, 20 GeoJSON featureCollection (pen 40), 22 rich-text HTML,
23 fill color, 25 legacy shape point list.

Pen behavior ([inferred] from boox-note-optimizer's fits against
device-exported PDFs; not re-validated here): 2 ballpoint / 15
highlighter constant width; 5 fountain w = th*1.37*(p/4095)^0.59; 21
marker w = th*2.35*(p/4095)^0.43; 22 charcoal ~ fountain envelope with
raster grain; 60/61 calligraphy filled polygons (approximated here as
variable-width strokes). Confidence per field: docs/formats/boox.md.
"""
from __future__ import annotations

import io
import json
import logging
import math
import re
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import ir

_logger = logging.getLogger(__name__)

FORMAT_ID = "boox"

# ---------------------------------------------------------------- wire

class WireError(ValueError):
    pass


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(buf):
            raise WireError("varint past end of buffer")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise WireError("varint too long")


def parse_message(buf: bytes) -> list[tuple[int, int, Any]]:
    """Schema-less protobuf walk -> [(field number, wire type, value)].
    wt0 -> int, wt1 -> f64 LE, wt2 -> bytes, wt5 -> f32 LE."""
    fields: list[tuple[int, int, Any]] = []
    pos = 0
    while pos < len(buf):
        key, pos = _read_varint(buf, pos)
        number, wtype = key >> 3, key & 7
        if number == 0:
            raise WireError("field number 0")
        if wtype == 0:
            value, pos = _read_varint(buf, pos)
        elif wtype == 1:
            if pos + 8 > len(buf):
                raise WireError("truncated fixed64")
            value = struct.unpack_from("<d", buf, pos)[0]
            pos += 8
        elif wtype == 2:
            length, pos = _read_varint(buf, pos)
            if pos + length > len(buf):
                raise WireError("truncated bytes field")
            value = buf[pos:pos + length]
            pos += length
        elif wtype == 5:
            if pos + 4 > len(buf):
                raise WireError("truncated fixed32")
            value = struct.unpack_from("<f", buf, pos)[0]
            pos += 4
        else:
            raise WireError(f"unsupported wire type {wtype}")
        fields.append((number, wtype, value))
    return fields


def _first(fields: list[tuple[int, int, Any]], number: int,
           wtype: int | None = None) -> Any:
    for n, w, v in fields:
        if n == number and (wtype is None or w == wtype):
            return v
    return None


def _json_field(fields: list[tuple[int, int, Any]], number: int) -> Any:
    raw = _first(fields, number, 2)
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------- points

POINT_STRUCT = struct.Struct(">ffBBHI")  # x, y, tilt_x, tilt_y, pressure, t_ms
_HEADER_SIZE = 76
_INDEX_ENTRY = 44


@dataclass
class RawPoint:
    x: float
    y: float
    tilt_x: int
    tilt_y: int
    pressure: int
    t_ms: int


def parse_points_blob(data: bytes) -> dict[str, list[RawPoint]]:
    """#points member -> {shapeUUID: points}, index order preserved."""
    if len(data) < _HEADER_SIZE + 4:
        raise WireError(f"points blob too short ({len(data)}B)")
    index_start = struct.unpack_from(">I", data, len(data) - 4)[0]
    if not _HEADER_SIZE <= index_start <= len(data) - 4:
        raise WireError(f"points index offset {index_start} out of range")
    strokes: dict[str, list[RawPoint]] = {}
    n_entries = (len(data) - 4 - index_start) // _INDEX_ENTRY
    for i in range(n_entries):
        pos = index_start + i * _INDEX_ENTRY
        uuid = data[pos:pos + 36].decode("ascii", errors="replace").strip()
        offset, size = struct.unpack_from(">II", data, pos + 36)
        if offset + size > index_start or size < 4:
            _logger.warning("boox: stroke %s entry out of range, skipped", uuid)
            continue
        pts = [RawPoint(*POINT_STRUCT.unpack_from(data, offset + 4 + j * 16))
               for j in range((size - 4) // 16)]
        if pts:
            strokes[uuid] = pts
    return strokes


# ---------------------------------------------------------------- shapes

@dataclass
class ShapeMeta:
    uuid: str
    page_id: str
    pen_type: int = 2
    thickness: float = 3.0
    argb: int = 0xFF000000
    fill_argb: int | None = None
    created: int = 0
    layer_id: int = 0
    matrix: tuple[float, ...] | None = None  # (a, b, tx, c, d, ty)
    bbox: dict | None = None
    pen_config: dict | None = None
    text: str | None = None
    rich_text: str | None = None
    text_style: dict | None = None
    extra_json: dict | None = None
    point_list: list[tuple[float, float]] = field(default_factory=list)


def _parse_matrix(fields: list[tuple[int, int, Any]]) -> tuple[float, ...] | None:
    j = _json_field(fields, 8)
    if j is None:
        return None
    values = j.get("values") if isinstance(j, dict) else j
    if isinstance(values, list) and len(values) >= 6:
        try:
            return tuple(float(v) for v in values[:6])
        except (TypeError, ValueError):
            return None
    return None


def parse_shape_message(msg: bytes, page_id: str) -> ShapeMeta | None:
    fields = parse_message(msg)
    uuid_raw = _first(fields, 1, 2)
    if not uuid_raw:
        return None
    meta = ShapeMeta(uuid=uuid_raw.decode("utf-8", errors="replace"),
                     page_id=page_id)
    meta.created = int(_first(fields, 2, 0) or 0)
    argb = _first(fields, 4, 0)
    if argb is not None:
        meta.argb = argb & 0xFFFFFFFF  # sign-extended Java int
    th = _first(fields, 5, 5)
    if th is not None and th > 0:
        meta.thickness = float(th)
    meta.layer_id = int(_first(fields, 6, 0) or 0)
    meta.bbox = _json_field(fields, 7)
    meta.matrix = _parse_matrix(fields)
    meta.text_style = _json_field(fields, 9)
    text_raw = _first(fields, 10, 2)
    if text_raw:
        meta.text = text_raw.decode("utf-8", errors="replace")
    meta.pen_config = _json_field(fields, 11)
    meta.pen_type = int(_first(fields, 12, 0) or 2)
    meta.extra_json = _json_field(fields, 20)
    rich_raw = _first(fields, 22, 2)
    if rich_raw:
        meta.rich_text = rich_raw.decode("utf-8", errors="replace")
    fill = _first(fields, 23, 0)
    if fill is not None:
        meta.fill_argb = fill & 0xFFFFFFFF
    plist = _first(fields, 25, 2)
    if plist and len(plist) > 4:
        for po in range(4, len(plist) - 15, 16):
            x, y = struct.unpack_from(">ff", plist, po)
            meta.point_list.append((x, y))
    return meta


def parse_shape_zip(data: bytes, page_id: str) -> list[ShapeMeta]:
    metas: list[ShapeMeta] = []
    with zipfile.ZipFile(io.BytesIO(data)) as inner:
        for name in inner.namelist():
            try:
                fields = parse_message(inner.read(name))
            except WireError as e:
                _logger.warning("boox: bad shape protobuf %s: %s", name, e)
                continue
            for n, w, v in fields:
                if n == 1 and w == 2:
                    meta = parse_shape_message(v, page_id)
                    if meta is not None:
                        metas.append(meta)
    return metas


# ---------------------------------------------------------------- note_info

@dataclass
class NoteMeta:
    note_id: str = ""
    title: str = ""
    page_list: list[str] = field(default_factory=list)
    page_info: dict[str, dict] = field(default_factory=dict)  # pid -> pageInfoMap entry
    default_rect: dict | None = None
    canvas_w: float = 0.0
    canvas_h: float = 0.0
    background: dict | None = None
    device: dict | None = None


def _unwrap_note_meta(data: bytes) -> list[bytes]:
    """note_info / note_tree wrap metadata message(s) at field 1; a bare
    metadata message has other fields too. Returns metadata messages."""
    try:
        fields = parse_message(data)
    except WireError:
        return []
    if fields and all(n == 1 and w == 2 for n, w, _ in fields):
        return [v for _, _, v in fields]
    return [data]


def parse_note_meta(msg: bytes) -> NoteMeta:
    fields = parse_message(msg)
    meta = NoteMeta()
    note_id = _first(fields, 1, 2)
    if note_id:
        meta.note_id = note_id.decode("utf-8", errors="replace")
    title = _first(fields, 6, 2)
    if title:
        meta.title = title.decode("utf-8", errors="replace")
    canvas = _json_field(fields, 12)
    if isinstance(canvas, dict):
        meta.default_rect = canvas.get("defaultPageRect")
        pim = canvas.get("pageInfoMap")
        if isinstance(pim, dict):
            meta.page_info = pim
    meta.background = _json_field(fields, 13)
    meta.device = _json_field(fields, 14)
    pages = _json_field(fields, 20)
    if isinstance(pages, dict):
        meta.page_list = [str(p) for p in pages.get("pageNameList") or []]
    meta.canvas_w = float(_first(fields, 22, 5) or 0.0)
    meta.canvas_h = float(_first(fields, 23, 5) or 0.0)
    return meta


def parse_page_models(data: bytes) -> dict[str, dict]:
    """pageModel/pb member -> {pageUUID: {"rect":..., "layer_list":...}}."""
    out: dict[str, dict] = {}
    try:
        fields = parse_message(data)
    except WireError:
        return out
    for n, w, v in fields:
        if n != 1 or w != 2:
            continue
        try:
            sub = parse_message(v)
        except WireError:
            continue
        pid_raw = _first(sub, 1, 2)
        if not pid_raw:
            continue
        pid = pid_raw.decode("utf-8", errors="replace")
        layers = _json_field(sub, 2)
        entry: dict[str, Any] = {"rect": _json_field(sub, 7)}
        if isinstance(layers, dict):
            entry["layer_list"] = layers.get("layerList")
        out[pid] = entry
    return out


# ---------------------------------------------------------------- styling

TOOL_FAMILY = {
    2: ir.ToolFamily.BALLPOINT,
    5: ir.ToolFamily.PEN,          # fountain
    15: ir.ToolFamily.HIGHLIGHTER,
    21: ir.ToolFamily.MARKER,
    22: ir.ToolFamily.SHADER,      # charcoal
    60: ir.ToolFamily.CALLIGRAPHY,
    61: ir.ToolFamily.CALLIGRAPHY,
}

#: pen type -> (k, exponent) for w = thickness * k * (p/pmax)^exp
#: [inferred] fitted by boox-note-optimizer against device PDF exports.
PRESSURE_WIDTH = {
    5: (1.37, 0.59),
    21: (2.35, 0.43),
    22: (1.37, 0.59),  # charcoal envelope ~ fountain
    60: (1.37, 0.59),  # calligraphy approximation
    61: (1.37, 0.59),
}

_MIN_WIDTH = 0.5
_MAX_PRESSURE = 4095.0
_TEXT_PEN_TYPES = (6, 16)


def _color(argb: int) -> tuple[ir.Color, float]:
    return (ir.Color(((argb >> 16) & 255) / 255.0,
                     ((argb >> 8) & 255) / 255.0,
                     (argb & 255) / 255.0),
            ((argb >> 24) & 255) / 255.0)


def _transform(meta: ShapeMeta,
               xs: list[float], ys: list[float]) -> tuple[list[float], list[float]]:
    if meta.matrix is None:
        return xs, ys
    a, b, tx, c, d, ty = meta.matrix
    return ([a * x + b * y + tx for x, y in zip(xs, ys)],
            [c * x + d * y + ty for x, y in zip(xs, ys)])


def _matrix_scale(meta: ShapeMeta) -> float:
    if meta.matrix is None:
        return 1.0
    a, b, _, c, d, _ = meta.matrix
    return (math.hypot(a, c) + math.hypot(b, d)) / 2.0


def _native_tool(meta: ShapeMeta) -> ir.NativeTool:
    params: dict[str, Any] = {"thickness": meta.thickness,
                              "argb": meta.argb, "layer_id": meta.layer_id}
    if meta.pen_config:
        params["pen_config"] = meta.pen_config
    if meta.matrix:
        params["matrix"] = list(meta.matrix)
    return ir.NativeTool(FORMAT_ID, meta.pen_type, params)


def _ink_stroke(meta: ShapeMeta, pts: list[RawPoint]) -> ir.Stroke:
    xs, ys = _transform(meta, [p.x for p in pts], [p.y for p in pts])
    pmax = _MAX_PRESSURE
    if meta.pen_config and meta.pen_config.get("maxPressure"):
        pmax = float(meta.pen_config["maxPressure"]) or _MAX_PRESSURE
    pressures = [min(p.pressure / pmax, 1.0) for p in pts]
    channels: dict[ir.Channel, list[float]] = {
        ir.Channel.PRESSURE: pressures,
        ir.Channel.TIMESTAMP: [p.t_ms / 1000.0 for p in pts],
        # tilt_x: azimuth, 256 units per full turn [inferred]
        ir.Channel.TILT_AZIMUTH: [p.tilt_x * 2.0 * math.pi / 256.0 for p in pts],
    }
    color, alpha = _color(meta.argb)
    thickness = meta.thickness * _matrix_scale(meta)
    family = TOOL_FAMILY.get(meta.pen_type, ir.ToolFamily.UNKNOWN)

    if meta.pen_type in PRESSURE_WIDTH:
        k, exp = PRESSURE_WIDTH[meta.pen_type]
        channels[ir.Channel.WIDTH] = [
            max(thickness * k * pr ** exp, _MIN_WIDTH) for pr in pressures]
        mode, width = ir.GeometryMode.STROKED_VARIABLE, None
    else:
        channels[ir.Channel.WIDTH] = [thickness] * len(pts)
        mode, width = ir.GeometryMode.STROKED_CONSTANT, thickness

    is_highlight = meta.pen_type == 15
    stroke = ir.Stroke(
        x=xs, y=ys,
        tool=ir.ToolRef(family=family, native=_native_tool(meta)),
        color=color,
        channels=channels,
        appearance=ir.StrokeAppearance(
            mode=mode,
            width=width,
            color=color,
            # device draws the highlighter at ~50% multiply [inferred]
            opacity=0.5 if is_highlight else alpha,
            blend=ir.BlendMode.MULTIPLY if is_highlight else ir.BlendMode.NORMAL,
            cap=ir.LineCap.ROUND,
            underlay=is_highlight,
        ),
        extra={"boox": {"shape_uuid": meta.uuid,
                        "tilt_y": [p.tilt_y for p in pts]}},
    )
    return stroke


def _polyline_stroke(meta: ShapeMeta, xs: list[float], ys: list[float],
                     width: float | None = None,
                     color: ir.Color | None = None,
                     alpha: float | None = None) -> ir.Stroke:
    base_color, base_alpha = _color(meta.argb)
    color = color or base_color
    alpha = base_alpha if alpha is None else alpha
    width = (width if width is not None
             else meta.thickness * _matrix_scale(meta)) or 1.0
    return ir.Stroke(
        x=xs, y=ys,
        tool=ir.ToolRef(family=ir.ToolFamily.PEN, native=_native_tool(meta)),
        color=color,
        channels={ir.Channel.WIDTH: [width] * len(xs)},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT, width=width,
            color=color, opacity=alpha, cap=ir.LineCap.ROUND,
        ),
        extra={"boox": {"shape_uuid": meta.uuid, "pen_type": meta.pen_type}},
    )


# --- geometric shapes (pen 40, GeoJSON in field 20) [inferred, untested
# against a device sample — none in the study corpus] -----------------------

def _sample_ellipse(x0: float, y0: float, x1: float, y1: float,
                    n: int = 48) -> tuple[list[float], list[float]]:
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    rx, ry = abs(x1 - x0) / 2.0, abs(y1 - y0) / 2.0
    ts = [2.0 * math.pi * i / n for i in range(n + 1)]
    return ([cx + rx * math.cos(t) for t in ts],
            [cy + ry * math.sin(t) for t in ts])


def _sample_quad_bezier(p0, p1, p2, n: int = 24) -> tuple[list[float], list[float]]:
    xs, ys = [], []
    for i in range(n + 1):
        t = i / n
        mt = 1.0 - t
        xs.append(mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0])
        ys.append(mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1])
    return xs, ys


def _geo_polylines(feature: dict) -> list[tuple[list[float], list[float]]]:
    geometry = feature.get("geometry") or {}
    gtype = geometry.get("type", "")
    coords = geometry.get("coordinates") or []
    sub = (feature.get("properties") or {}).get("subType", "")
    out: list[tuple[list[float], list[float]]] = []

    def _xy(pts):
        return ([float(p[0]) for p in pts], [float(p[1]) for p in pts])

    try:
        if gtype in ("LineString", "DirectionLine", "BidirectionalLine"):
            if len(coords) >= 2:
                out.append(_xy(coords))
        elif gtype == "MultiLineString":
            for line in coords:
                if len(line) >= 2:
                    out.append(_xy(line))
        elif gtype == "Polygon":
            # rings of [start, end] segment pairs; vertex = pair[0]
            for ring in coords:
                verts = [seg[0] for seg in ring if seg]
                if len(verts) >= 2:
                    verts.append(verts[0])
                    out.append(_xy(verts))
        elif gtype == "MultiPoint" and sub == "Oval" and len(coords) >= 2:
            out.append(_sample_ellipse(coords[0][0], coords[0][1],
                                       coords[1][0], coords[1][1]))
        elif gtype == "MultiPoint" and sub == "Curve" and len(coords) >= 3:
            out.append(_sample_quad_bezier(coords[0], coords[1], coords[2]))
        else:
            _logger.warning("boox: unsupported geometry %s/%s skipped",
                            gtype, sub)
    except (TypeError, IndexError, ValueError):
        _logger.warning("boox: malformed geometry %s skipped", gtype)
    return out


def _geo_strokes(meta: ShapeMeta) -> list[ir.Stroke]:
    if not isinstance(meta.extra_json, dict):
        return []
    fc_str = meta.extra_json.get("featureCollection")
    if not fc_str:
        return []
    try:
        fc = json.loads(fc_str) if isinstance(fc_str, str) else fc_str
        features = list(fc.get("features") or [])
    except (json.JSONDecodeError, AttributeError):
        return []
    strokes: list[ir.Stroke] = []
    while features:
        feat = features.pop(0)
        if not isinstance(feat, dict):
            continue
        geometry = feat.get("geometry") or {}
        if geometry.get("type") == "FeatureCollection" or feat.get("features"):
            features.extend(feat.get("features") or [])
            continue
        props = feat.get("properties") or {}
        stroke_attr = props.get("strokeAttr") or {}
        width = stroke_attr.get("lineWidth")
        color = alpha = None
        if isinstance(stroke_attr.get("color"), int):
            color, alpha = _color(stroke_attr["color"] & 0xFFFFFFFF)
        for xs, ys in _geo_polylines(feat):
            xs, ys = _transform(meta, xs, ys)
            strokes.append(_polyline_stroke(
                meta, xs, ys,
                width=float(width) * _matrix_scale(meta) if width else None,
                color=color, alpha=alpha))
    return strokes


# --- legacy point-list shapes (field 25; pen 0/1/7/8/...) [inferred] --------

_CLOSED_POLY_PENS = frozenset({8, 10, 11, 12, 17, 18, 24, 26, 27})


def _point_list_strokes(meta: ShapeMeta) -> list[ir.Stroke]:
    pts = meta.point_list
    if len(pts) < 2:
        return []
    if meta.pen_type == 0:  # oval bounding box
        xs, ys = _sample_ellipse(pts[0][0], pts[0][1], pts[1][0], pts[1][1])
    elif meta.pen_type == 1:  # rectangle corners
        (x0, y0), (x1, y1) = pts[0], pts[1]
        xs = [x0, x1, x1, x0, x0]
        ys = [y0, y0, y1, y1, y0]
    elif meta.pen_type in _CLOSED_POLY_PENS and len(pts) >= 3:
        xs = [p[0] for p in pts] + [pts[0][0]]
        ys = [p[1] for p in pts] + [pts[0][1]]
    else:  # line (7, 28) / polyline (31) / unknown
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
    xs, ys = _transform(meta, xs, ys)
    return [_polyline_stroke(meta, xs, ys)]


def _text_block(meta: ShapeMeta) -> ir.TextBlock | None:
    text = meta.text
    if not text and meta.rich_text:
        text = re.sub(r"<[^>]+>", "", meta.rich_text).strip()
    if not text:
        return None
    x = y = 0.0
    if isinstance(meta.bbox, dict):
        x = float(meta.bbox.get("left", 0.0))
        y = float(meta.bbox.get("top", 0.0))
    color, _ = _color(meta.argb)
    size = None
    if isinstance(meta.text_style, dict) and meta.text_style.get("textSize"):
        size = float(meta.text_style["textSize"])
    return ir.TextBlock(x=x, y=y, text=text, font_size=size, color=color,
                        extra={"boox": {"shape_uuid": meta.uuid,
                                        "pen_type": meta.pen_type}})


# ---------------------------------------------------------------- assembly

def _page_bounds(pid: str, meta: NoteMeta, page_models: dict[str, dict],
                 strokes: list[ir.Stroke]) -> ir.Rect:
    info = meta.page_info.get(pid)
    if isinstance(info, dict) and info.get("width") and info.get("height"):
        return ir.Rect(0.0, 0.0, float(info["width"]), float(info["height"]))
    rect = (page_models.get(pid) or {}).get("rect")
    if not isinstance(rect, dict):
        rect = meta.default_rect
    if isinstance(rect, dict) and rect.get("right"):
        return ir.Rect(float(rect.get("left", 0.0)), float(rect.get("top", 0.0)),
                       float(rect["right"]), float(rect.get("bottom", 0.0)))
    if meta.canvas_w and meta.canvas_h:
        return ir.Rect(0.0, 0.0, meta.canvas_w, meta.canvas_h)
    # last resort: point extents [inferred]
    xs = [x for s in strokes for x in s.x]
    ys = [y for s in strokes for y in s.y]
    if xs:
        return ir.Rect(0.0, 0.0, max(xs) + 10.0, max(ys) + 10.0)
    return ir.Rect(0.0, 0.0, 1860.0, 2480.0)


def _build_page(pid: str, meta: NoteMeta, page_models: dict[str, dict],
                shape_metas: list[ShapeMeta],
                points: dict[str, list[RawPoint]]) -> ir.Page:
    strokes_by_uuid: dict[str, list[ir.Stroke]] = {}
    texts_by_uuid: dict[str, ir.TextBlock] = {}
    meta_uuids = {m.uuid for m in shape_metas}
    for sm in sorted(shape_metas, key=lambda m: m.created):
        if sm.pen_type in _TEXT_PEN_TYPES:
            tb = _text_block(sm)
            if tb is not None:
                texts_by_uuid[sm.uuid] = tb
            continue
        if sm.pen_type == 40:
            strokes_by_uuid[sm.uuid] = _geo_strokes(sm)
            continue
        pts = points.get(sm.uuid)
        if pts:
            strokes_by_uuid[sm.uuid] = [_ink_stroke(sm, pts)]
        elif sm.point_list:
            strokes_by_uuid[sm.uuid] = _point_list_strokes(sm)
    # points with no shape metadata: keep the ink, default style
    for uuid, pts in points.items():
        if uuid not in meta_uuids:
            orphan = ShapeMeta(uuid=uuid, page_id=pid)
            stroke = _ink_stroke(orphan, pts)
            stroke.tool.family = ir.ToolFamily.UNKNOWN
            strokes_by_uuid[uuid] = [stroke]

    # z-order: layerList order from pageInfoMap (fallback pageModel), then
    # creation timestamp within a layer [inferred]
    layer_list = None
    info = meta.page_info.get(pid)
    if isinstance(info, dict):
        layer_list = info.get("layerList")
    if not layer_list:
        layer_list = (page_models.get(pid) or {}).get("layer_list")
    meta_by_uuid = {m.uuid: m for m in shape_metas}
    layers: list[ir.Layer] = []
    assigned: set[str] = set()
    for entry in layer_list or []:
        if not isinstance(entry, dict):
            continue
        lid = entry.get("id", 0)
        layer = ir.Layer(name=str(lid), visible=bool(entry.get("show", True)))
        for uuid, built in strokes_by_uuid.items():
            m = meta_by_uuid.get(uuid)
            if m is not None and m.layer_id == lid:
                layer.strokes.extend(built)
                assigned.add(uuid)
        for uuid, tb in texts_by_uuid.items():
            m = meta_by_uuid.get(uuid)
            if m is not None and m.layer_id == lid:
                layer.texts.append(tb)
                assigned.add(uuid)
        layers.append(layer)
    rest = ir.Layer()
    for uuid, built in strokes_by_uuid.items():
        if uuid not in assigned:
            rest.strokes.extend(built)
    for uuid, tb in texts_by_uuid.items():
        if uuid not in assigned:
            rest.texts.append(tb)
    if rest.strokes or rest.texts or not layers:
        layers.append(rest)

    all_strokes = [s for layer in layers for s in layer.strokes]
    return ir.Page(
        bounds=_page_bounds(pid, meta, page_models, all_strokes),
        point_scale=1.0,  # coordinates are PDF points 1:1 [verified upstream]
        layers=layers,
        extra={"boox": {"page_id": pid}},
    )


def _page_id_from_path(name: str, anchor: str) -> str | None:
    parts = name.split("/")
    try:
        i = parts.index(anchor)
    except ValueError:
        return None
    if i + 1 >= len(parts):
        return None
    return parts[i + 1].split("#")[0]


def read_zip(zf: zipfile.ZipFile, title: str) -> ir.Document:
    names = [n for n in zf.namelist() if not n.endswith("/")]

    # note metadata: per-note note/pb/note_info (+ optional root note_tree)
    metas: list[NoteMeta] = []
    roots: list[str] = []
    for name in names:
        if name.endswith("/note/pb/note_info"):
            root = name.split("/")[0]
            if root not in roots:
                roots.append(root)
                for msg in _unwrap_note_meta(zf.read(name)):
                    try:
                        metas.append(parse_note_meta(msg))
                    except WireError as e:
                        _logger.warning("boox: bad note_info in %s: %s",
                                        root, e)
    if not metas and "note_tree" in names:  # multi-note tree [inferred]
        for msg in _unwrap_note_meta(zf.read("note_tree")):
            try:
                metas.append(parse_note_meta(msg))
            except WireError:
                continue
        roots = [m.note_id for m in metas if m.note_id]

    pages: list[ir.Page] = []
    doc_meta: dict[str, Any] = {"notes": []}
    for root, meta in zip(roots, metas):
        prefix = f"{root}/"
        page_models: dict[str, dict] = {}
        points_by_page: dict[str, dict[str, list[RawPoint]]] = {}
        shapes_by_page: dict[str, list[ShapeMeta]] = {}
        for name in names:
            if not name.startswith(prefix) or "/stash/" in name:
                continue
            if "/pageModel/pb/" in name:
                page_models.update(parse_page_models(zf.read(name)))
            elif name.endswith("#points") and "/point/" in name:
                pid = _page_id_from_path(name, "point")
                if pid is None:
                    continue
                try:
                    strokes = parse_points_blob(zf.read(name))
                except WireError as e:
                    _logger.warning("boox: bad points blob %s: %s", name, e)
                    continue
                points_by_page.setdefault(pid, {}).update(strokes)
            elif name.endswith(".zip") and "/shape/" in name:
                pid = _page_id_from_path(name, "shape")
                if pid is None:
                    continue
                try:
                    metas_ = parse_shape_zip(zf.read(name), pid)
                except (zipfile.BadZipFile, WireError) as e:
                    _logger.warning("boox: bad shape zip %s: %s", name, e)
                    continue
                shapes_by_page.setdefault(pid, []).extend(metas_)

        page_order = [p for p in meta.page_list]
        for pid in list(points_by_page) + list(shapes_by_page):
            if pid not in page_order:
                page_order.append(pid)
        for pid in page_order:
            pages.append(_build_page(
                pid, meta, page_models,
                shapes_by_page.get(pid, []),
                points_by_page.get(pid, {})))
        doc_meta["notes"].append({
            "note_id": meta.note_id, "title": meta.title,
            "device": meta.device, "background": meta.background,
        })

    doc_title = metas[0].title if len(metas) == 1 and metas[0].title else title
    return ir.Document(
        format_id=FORMAT_ID,
        title=doc_title,
        pages=pages,
        metadata=doc_meta,
    )


class BooxReader:
    format_id = FORMAT_ID
    extensions = (".note",)

    def detect(self, path: Path) -> bool:
        # .note is also Supernote (binary) and Notability (zip with
        # Session.plist); Boox is a zip with <noteId>/note/pb/note_info
        # (or a root note_tree for multi-note archives).
        try:
            if not zipfile.is_zipfile(path):
                return False
            with zipfile.ZipFile(path) as zf:
                return any(n.endswith("/note/pb/note_info")
                           or n == "note_tree"
                           for n in zf.namelist())
        except (OSError, zipfile.BadZipFile):
            return False

    def read(self, path: Path) -> ir.Document:
        with zipfile.ZipFile(path) as zf:
            return read_zip(zf, title=path.stem)
