"""Wacom Universal Ink Model (.uim) -> IR.

Format facts and protobuf field numbers derived from Wacom's Apache-2.0
universal-ink-library (github.com/Wacom-Developer/universal-ink-library;
`uim/codec/parser/uim.py`, `decoder_3_0_0.py`, `decoder_3_1_0.py` and the
generated schema in `uim/codec/format/`), plus the public spec at
developer-docs.wacom.com. This is an independent stdlib-only
implementation; nothing is imported from the Apache library at runtime.

Container `[verified against corpus samples, both versions]`:

  RIFF little-endian, form type `UINK`. First chunk `HEAD`:
  3 version bytes (major, minor, patch).
  - v3.0.0: HEAD is just the version (3 bytes + pad); one `DATA` chunk
    follows holding a single protobuf `InkObject` message.
  - v3.1.0: HEAD continues with 1 reserved byte + N 8-byte chunk
    descriptions (major, minor, patch, content-type, compression,
    3 reserved); then N chunks: `PRPS` (properties), `INPT` (input/sensor
    data), `BRSH` (brush definitions), `INKD` (ink/stroke data), `KNWG`
    (semantic triples), `INKS` (ink tree structure). Content-type 1 =
    protobuf; compression 0 = none, 1 = zip, 2 = LZMA (compressed chunks
    are `[unknown]` - no samples; we attempt zlib/lzma and skip on
    failure - the Apache library refuses them outright).
  All chunks are padded to even sizes.

Geometry `[verified]`: strokes carry Catmull-Rom spline control points
(splineX/Y, per-point size/color). Control points lie ON the curve; the
first and last are duplicated phantom endpoints, which we drop when they
equal their neighbor (mirrors the Apache library's
`remove_duplicates_at_ends`). We emit the control polygon as the
polyline - a piecewise-linear flattening `[inferred]`. v3.1
`splineCompressed` stores zigzag varint deltas scaled by
10^precision, with per-purpose precisions packed in 4-bit fields of the
stroke's `precisions` value (position/size/rotation/scale/offset,
low to high) `[verified]`.

Sensor data `[verified]`: `INPT` carries per-channel metadata (type URI
`will://input/3.0/channel/<X|Y|Timestamp|Pressure|Azimuth|Altitude|...>`,
resolution, min/max, precision) and per-stroke `SensorData` records with
zigzag-varint delta-encoded channel values; decoded value =
cumsum(delta) / (resolution * 10^precision). Timestamps additionally
start from the record's uint64 epoch-milliseconds timestamp. Strokes
reference their SensorData by id, with `sensorDataOffset`/`
sensorDataMapping` aligning spline points to sensor samples (mirrored
from the Apache library's `get_sensor_point`).

Units: spline coordinates are app "virtual pixels"; sample X/Y sensor
channels declare resolution 3779.5275590592/m = exactly 96 dpi, i.e. the
WILL DIP (1/96 in), so `point_scale = 72/96 = 0.75` `[inferred]`.

Not parsed: `KNWG` semantic triples, ink-tree grouping/views (strokes
are read in InkData order), raster-brush textures.
"""
from __future__ import annotations

import logging
import struct
from pathlib import Path

from .. import ir

_logger = logging.getLogger(__name__)

FORMAT_ID = "uim"

_CHANNEL_URI_PREFIX = "will://input/3.0/channel/"
_POINT_SCALE = 72.0 / 96.0  # DIP -> PDF points [inferred]


class UimError(ValueError):
    pass


# --------------------------------------------------------------- wire walker

def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(buf):
            raise UimError("varint past end of buffer")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise UimError("varint too long")


def zigzag(value: int) -> int:
    return (value >> 1) ^ -(value & 1)


def parse_message(buf: bytes) -> dict[int, list[tuple[int, object]]]:
    """Protobuf wire walk -> {field number: [(wire type, raw value), ...]}.

    Varints stay ints; fixed32/fixed64/length-delimited stay raw bytes
    (interpretation needs the schema, applied by the typed getters below).
    """
    fields: dict[int, list[tuple[int, object]]] = {}
    pos = 0
    while pos < len(buf):
        key, pos = read_varint(buf, pos)
        number, wtype = key >> 3, key & 7
        if number == 0:
            raise UimError("field number 0")
        value: object
        if wtype == 0:
            value, pos = read_varint(buf, pos)
        elif wtype == 1:
            if pos + 8 > len(buf):
                raise UimError("truncated fixed64")
            value = buf[pos:pos + 8]
            pos += 8
        elif wtype == 2:
            length, pos = read_varint(buf, pos)
            if pos + length > len(buf):
                raise UimError("truncated bytes field")
            value = buf[pos:pos + length]
            pos += length
        elif wtype == 5:
            if pos + 4 > len(buf):
                raise UimError("truncated fixed32")
            value = buf[pos:pos + 4]
            pos += 4
        else:
            raise UimError(f"unsupported wire type {wtype}")
        fields.setdefault(number, []).append((wtype, value))
    return fields


Msg = dict[int, list[tuple[int, object]]]


def _bytes(m: Msg, n: int) -> bytes:
    for wt, v in m.get(n, ()):
        if wt == 2:
            return v  # type: ignore[return-value]
    return b""


def _str(m: Msg, n: int) -> str:
    return _bytes(m, n).decode("utf-8", "replace")


def _uint(m: Msg, n: int, default: int = 0) -> int:
    for wt, v in m.get(n, ()):
        if wt == 0:
            return v  # type: ignore[return-value]
    return default


def _sint(m: Msg, n: int, default: int = 0) -> int:
    for wt, v in m.get(n, ()):
        if wt == 0:
            return zigzag(v)  # type: ignore[arg-type]
    return default


def _float(m: Msg, n: int, default: float = 0.0) -> float:
    for wt, v in m.get(n, ()):
        if wt == 5:
            return struct.unpack("<f", v)[0]  # type: ignore[arg-type]
    return default


def _double(m: Msg, n: int, default: float = 0.0) -> float:
    for wt, v in m.get(n, ()):
        if wt == 1:
            return struct.unpack("<d", v)[0]  # type: ignore[arg-type]
    return default


def _floats(m: Msg, n: int) -> list[float]:
    """repeated float: packed (wt 2) and/or unpacked (wt 5)."""
    out: list[float] = []
    for wt, v in m.get(n, ()):
        if wt == 5:
            out.append(struct.unpack("<f", v)[0])  # type: ignore[arg-type]
        elif wt == 2:
            out.extend(struct.unpack(f"<{len(v) // 4}f", v))  # type: ignore[arg-type]
    return out


def _varints(m: Msg, n: int) -> list[int]:
    """repeated *int32: packed (wt 2) and/or unpacked (wt 0)."""
    out: list[int] = []
    for wt, v in m.get(n, ()):
        if wt == 0:
            out.append(v)  # type: ignore[arg-type]
        elif wt == 2:
            pos = 0
            while pos < len(v):  # type: ignore[arg-type]
                val, pos = read_varint(v, pos)  # type: ignore[arg-type]
                out.append(val)
    return out


def _sints(m: Msg, n: int) -> list[int]:
    return [zigzag(v) for v in _varints(m, n)]


def _msgs(m: Msg, n: int) -> list[Msg]:
    return [parse_message(v) for wt, v in m.get(n, ()) if wt == 2]


def _msg(m: Msg, n: int) -> Msg:
    sub = _msgs(m, n)
    return sub[0] if sub else {}


# --------------------------------------------------------------- RIFF layer

def _riff_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    """RIFF/UINK -> [(chunk id, chunk payload), ...] (HEAD included)."""
    if data[:4] != b"RIFF" or data[8:12] != b"UINK":
        raise UimError("not a RIFF/UINK (Universal Ink Model) file")
    total = struct.unpack_from("<I", data, 4)[0]
    end = min(len(data), 8 + total)
    chunks: list[tuple[bytes, bytes]] = []
    pos = 12
    while pos + 8 <= end:
        cid = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8:pos + 8 + size]
        if len(body) < size:
            raise UimError(f"truncated chunk {cid!r}")
        chunks.append((cid, body))
        pos += 8 + size + (size & 1)  # word alignment
    return chunks


def _decompress(body: bytes, compression: int) -> bytes | None:
    """Chunk compression flag: 0 none, 1 zip, 2 LZMA. [unknown] - no
    compressed samples exist; best-effort stdlib attempt."""
    if compression == 0:
        return body
    try:
        if compression == 1:
            import zlib
            return zlib.decompress(body)
        if compression == 2:
            import lzma
            return lzma.decompress(body)
    except Exception:  # noqa: BLE001 - skip undecodable chunk
        pass
    _logger.warning("uim: skipping chunk with compression type %d", compression)
    return None


# ------------------------------------------------------------- sensor layer

def _delta_decode(ints: list[int], precision: int, resolution: float = 1.0,
                  start: float = 0.0) -> list[float]:
    """Cumulative-sum decode of scaled deltas (mirrors the Apache
    library's CodecDecoder.__decode__, including its start-value quirk)."""
    factor = resolution * 10.0 ** precision
    last = start / factor if start else 0.0
    out: list[float] = []
    for v in ints:
        last = last + v / factor
        out.append(last)
    return out


class _SensorChannel:
    __slots__ = ("kind", "resolution", "vmin", "vmax", "precision")

    def __init__(self, kind: str, resolution: float, vmin: float,
                 vmax: float, precision: int):
        self.kind = kind  # URI tail: "X", "Pressure", "Timestamp", ...
        self.resolution = resolution
        self.vmin = vmin
        self.vmax = vmax
        self.precision = precision


def _parse_channels(input_context_data: Msg) -> dict[bytes, _SensorChannel]:
    """SensorContext tree -> {channel id: metadata}. Channel ids are
    globally unique, so the context indirection can be flattened."""
    channels: dict[bytes, _SensorChannel] = {}
    for sensor_context in _msgs(input_context_data, 5):
        for scc in _msgs(sensor_context, 2):
            for ch in _msgs(scc, 2):  # SensorChannel
                uri = _str(ch, 2)
                kind = uri.removeprefix(_CHANNEL_URI_PREFIX)
                channels[_bytes(ch, 1)] = _SensorChannel(
                    kind,
                    _double(ch, 4, 1.0) or 1.0,
                    _float(ch, 5),
                    _float(ch, 6),
                    _uint(ch, 7),
                )
    return channels


def _parse_sensor_data(input_data: Msg) -> dict[bytes, dict[str, list[float]]]:
    """InputData -> {sensor data id: {channel kind: decoded values}}."""
    channels = _parse_channels(_msg(input_data, 1))
    out: dict[bytes, dict[str, list[float]]] = {}
    for sd in _msgs(input_data, 2):  # SensorData
        timestamp = _uint(sd, 4)
        record: dict[str, list[float]] = {}
        for dc in _msgs(sd, 5):  # ChannelData
            meta = channels.get(_bytes(dc, 1))
            if meta is None:
                continue
            raw = _sints(dc, 2)
            start = float(timestamp) if meta.kind == "Timestamp" else 0.0
            record[meta.kind] = _delta_decode(raw, meta.precision,
                                              meta.resolution, start)
            if meta.kind == "Pressure" and meta.vmax > meta.vmin:
                record["Pressure"] = [
                    min(1.0, max(0.0, (v - meta.vmin) / (meta.vmax - meta.vmin)))
                    for v in record["Pressure"]
                ]
        out[_bytes(sd, 1)] = record
    return out


def _sensor_index(i: int, offset: int, mapping: list[int], n: int) -> int:
    """Spline point index -> sensor sample index (mirrors the Apache
    library's Stroke.get_sensor_point)."""
    if offset == 0 and i > 0:
        i -= 1
    if mapping:
        return mapping[i] if i < len(mapping) else mapping[-1]
    return min(offset + i, n - 1)


# ------------------------------------------------------------- stroke layer

class _RawStroke:
    """Version-neutral decoded stroke, pre-IR."""

    def __init__(self) -> None:
        self.x: list[float] = []
        self.y: list[float] = []
        self.sizes: list[float] = []
        self.red: list[int] = []
        self.green: list[int] = []
        self.blue: list[int] = []
        self.alpha: list[int] = []
        self.color: tuple[float, float, float, float] | None = None
        self.size: float = 0.0
        self.brush_uri: str = ""
        self.render_mode_uri: str = ""
        self.sensor_id: bytes = b""
        self.sensor_offset: int = 0
        self.sensor_mapping: list[int] = []


def _rgba_int(value: int) -> tuple[float, float, float, float]:
    value &= 0xFFFFFFFF
    return (((value >> 24) & 255) / 255.0, ((value >> 16) & 255) / 255.0,
            ((value >> 8) & 255) / 255.0, (value & 255) / 255.0)


def _props_310(m: Msg) -> tuple[tuple[float, float, float, float] | None, float]:
    """v3.1 PathPointProperties -> (RGBA, size). color is a zigzag-encoded
    RGBA int (R in the high byte)."""
    color = None
    if 1 in m:
        color = _rgba_int(_sint(m, 1))
    return color, _float(m, 2)


def _strokes_310(ink_data: Msg) -> list[_RawStroke]:
    brush_uris = [v.decode("utf-8", "replace")
                  for wt, v in ink_data.get(4, ()) if wt == 2]
    render_uris = [v.decode("utf-8", "replace")
                   for wt, v in ink_data.get(5, ()) if wt == 2]
    prop_table = [_props_310(p) for p in _msgs(ink_data, 6)]

    out: list[_RawStroke] = []
    for s in _msgs(ink_data, 1):
        r = _RawStroke()
        if 5 in s:  # SplineData: plain packed floats
            sd = _msg(s, 5)
            r.x = _floats(sd, 1)
            r.y = _floats(sd, 2)
            r.sizes = _floats(sd, 8)
        elif 6 in s:  # SplineCompressed: zigzag deltas * 10^precision
            sd = _msg(s, 6)
            precisions = _sint(s, 2)
            pos_prec = precisions & 0xF
            size_prec = (precisions >> 4) & 0xF
            r.x = _delta_decode(_sints(sd, 1), pos_prec)
            r.y = _delta_decode(_sints(sd, 2), pos_prec)
            r.sizes = _delta_decode(_sints(sd, 8), size_prec)
        else:
            continue
        sd = _msg(s, 5) if 5 in s else _msg(s, 6)
        r.red, r.green, r.blue, r.alpha = (
            _varints(sd, 4), _varints(sd, 5), _varints(sd, 6), _varints(sd, 7))
        props_index = _uint(s, 7)
        if props_index and props_index <= len(prop_table):
            r.color, r.size = prop_table[props_index - 1]
        elif 8 in s:
            r.color, r.size = _props_310(_msg(s, 8))
        brush_index = _uint(s, 9)
        if brush_index and brush_index <= len(brush_uris):
            r.brush_uri = brush_uris[brush_index - 1]
        elif 10 in s:
            r.brush_uri = _str(s, 10)
        render_index = _uint(s, 11)
        if render_index and render_index <= len(render_uris):
            r.render_mode_uri = render_uris[render_index - 1]
        elif 12 in s:
            r.render_mode_uri = _str(s, 12)
        r.sensor_offset = _uint(s, 14)
        r.sensor_id = _bytes(s, 15)
        r.sensor_mapping = _varints(s, 16)
        out.append(r)
    return out


def _strokes_300(ink_data: Msg) -> list[_RawStroke]:
    out: list[_RawStroke] = []
    for s in _msgs(ink_data, 1):
        r = _RawStroke()
        r.x = _floats(s, 4)
        r.y = _floats(s, 5)
        # per-point colors are floats 0-1 in v3.0
        r.red = [int(v * 255) for v in _floats(s, 7)]
        r.green = [int(v * 255) for v in _floats(s, 8)]
        r.blue = [int(v * 255) for v in _floats(s, 9)]
        r.alpha = [int(v * 255) for v in _floats(s, 10)]
        r.sizes = _floats(s, 11)
        style = _msg(s, 22)
        props = _msg(style, 1)  # Float32-wrapped PathPointProperties
        if props:
            r.size = _float(_msg(props, 1), 1)
            rgba = tuple(_float(_msg(props, n), 1, 0.0) for n in (2, 3, 4))
            alpha = _float(_msg(props, 5), 1, 1.0) if 5 in props else 1.0
            r.color = (*rgba, alpha)  # type: ignore[assignment]
        r.brush_uri = _str(style, 2)
        r.render_mode_uri = _str(style, 4)
        r.sensor_offset = _uint(s, 19)
        r.sensor_id = _bytes(s, 20)
        r.sensor_mapping = _varints(s, 21)
        out.append(r)
    return out


# ----------------------------------------------------------------- assembly

def _tool_family(brush_uri: str, raster_brushes: set[str]) -> ir.ToolFamily:
    low = brush_uri.lower()
    if "highlight" in low:
        return ir.ToolFamily.HIGHLIGHTER
    if brush_uri in raster_brushes:
        return ir.ToolFamily.UNKNOWN  # particle brush; no clean analogue
    if brush_uri:
        return ir.ToolFamily.PEN  # vector brushes render pen-like nibs
    return ir.ToolFamily.UNKNOWN


def _brush_names(brushes: Msg) -> tuple[set[str], set[str]]:
    """Brushes chunk -> (vector brush names, raster brush names)."""
    vector = {_str(b, 1) for b in _msgs(brushes, 1)}
    raster = {_str(b, 1) for b in _msgs(brushes, 2)}
    return vector, raster


def _ir_stroke(r: _RawStroke,
               sensors: dict[bytes, dict[str, list[float]]],
               raster_brushes: set[str],
               transform: tuple[float, float, float, float, float, float],
               ) -> ir.Stroke | None:
    n = len(r.x)
    if n == 0 or len(r.y) != n:
        return None

    # Drop duplicated phantom Catmull-Rom endpoints (Apache library's
    # remove_duplicates_at_ends behavior).
    start, end = 0, n
    if n >= 2 and r.x[0] == r.x[1] and r.y[0] == r.y[1]:
        start = 1
    if end - start >= 2 and r.x[-1] == r.x[-2] and r.y[-1] == r.y[-2]:
        end -= 1

    m00, m01, m03, m10, m11, m13 = transform
    xs = [m00 * r.x[i] + m01 * r.y[i] + m03 for i in range(start, end)]
    ys = [m10 * r.x[i] + m11 * r.y[i] + m13 for i in range(start, end)]

    channels: dict[ir.Channel, list[float]] = {}
    if len(r.sizes) == n:
        channels[ir.Channel.WIDTH] = r.sizes[start:end]
    if len(r.alpha) == n and len(set(r.alpha)) > 1:
        channels[ir.Channel.ALPHA] = [a / 255.0 for a in r.alpha[start:end]]

    record = sensors.get(r.sensor_id, {})
    for kind, channel in (("Pressure", ir.Channel.PRESSURE),
                          ("Azimuth", ir.Channel.TILT_AZIMUTH),
                          ("Altitude", ir.Channel.TILT_ALTITUDE),
                          ("Timestamp", ir.Channel.TIMESTAMP)):
        values = record.get(kind)
        if not values:
            continue
        aligned = [values[_sensor_index(i, r.sensor_offset, r.sensor_mapping,
                                        len(values))]
                   for i in range(n)][start:end]
        if channel is ir.Channel.TIMESTAMP and aligned:
            t0 = aligned[0]
            aligned = [t - t0 for t in aligned]  # seconds since stroke start
        channels[channel] = aligned

    # Color: constant style color, else first per-point color [inferred:
    # per-point color gradients collapse to their first value].
    if r.color is not None:
        cr, cg, cb, ca = r.color
    elif len(r.red) == n and len(r.green) == n and len(r.blue) == n:
        cr, cg, cb = r.red[start] / 255.0, r.green[start] / 255.0, \
            r.blue[start] / 255.0
        ca = r.alpha[start] / 255.0 if len(r.alpha) == n else 1.0
    else:
        cr = cg = cb = 0.0
        ca = 1.0
    color = ir.Color(cr, cg, cb)

    widths = channels.get(ir.Channel.WIDTH)
    variable = bool(widths) and (max(widths) - min(widths)) > 1e-6
    width = None if variable else (
        widths[0] if widths else (r.size if r.size > 0 else 1.0))

    family = _tool_family(r.brush_uri, raster_brushes)
    is_highlight = family is ir.ToolFamily.HIGHLIGHTER
    return ir.Stroke(
        x=xs, y=ys,
        tool=ir.ToolRef(
            family=family,
            native=ir.NativeTool(FORMAT_ID, r.brush_uri or "unknown", {
                "brush_uri": r.brush_uri,
                "render_mode_uri": r.render_mode_uri,
                "size": r.size,
            }),
        ),
        color=color,
        channels=channels,
        appearance=ir.StrokeAppearance(
            mode=(ir.GeometryMode.STROKED_VARIABLE if variable
                  else ir.GeometryMode.STROKED_CONSTANT),
            width=width,
            color=color,
            opacity=ca,
            cap=ir.LineCap.ROUND,
            underlay=is_highlight,
            blend=ir.BlendMode.MULTIPLY if is_highlight else ir.BlendMode.NORMAL,
        ),
    )


def _transform_310(ink_data: Msg) -> tuple[float, float, float, float, float, float]:
    """InkData.transform (Matrix, 2D affine part) -> (m00,m01,m03,m10,m11,m13).
    Identity when absent. [inferred: no transformed sample exists]"""
    t = _msg(ink_data, 3)
    if not t:
        scale = _float(ink_data, 2, 0.0) or 1.0  # unitScaleFactor fallback
        return (scale, 0.0, 0.0, 0.0, scale, 0.0)
    m00 = _float(t, 1, 0.0)
    m11 = _float(t, 6, 0.0)
    if m00 == 0.0 and m11 == 0.0:  # all-zero message => identity
        return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    return (m00, _float(t, 2), _float(t, 4),
            _float(t, 5), m11, _float(t, 8))


def _transform_300(ink_object: Msg) -> tuple[float, float, float, float, float, float]:
    t = _msg(ink_object, 7)
    if not t:
        return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    m00 = _float(t, 1, 0.0)
    m11 = _float(t, 6, 0.0)
    if m00 == 0.0 and m11 == 0.0:
        return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    return (m00, _float(t, 2), _float(t, 4),
            _float(t, 5), m11, _float(t, 8))


def _build_document(strokes: list[ir.Stroke], properties: dict[str, str],
                    version: str, title: str) -> ir.Document:
    xs = [x for s in strokes for x in s.x]
    ys = [y for s in strokes for y in s.y]
    if xs:
        bounds = ir.Rect(min(0.0, min(xs)), min(0.0, min(ys)),
                         max(xs), max(ys))
    else:
        bounds = ir.Rect(0.0, 0.0, 1000.0, 1000.0)
    for key in ("Title", "title", "name", "Author"):
        if properties.get(key):
            title = title or properties[key]
            break
    return ir.Document(
        format_id=FORMAT_ID,
        title=title,
        pages=[ir.Page(bounds=bounds, point_scale=_POINT_SCALE,
                       layers=[ir.Layer(strokes=strokes)])],
        metadata={"uim_version": version, "properties": properties},
    )


def read_uim(data: bytes, title: str = "") -> ir.Document:
    chunks = _riff_chunks(data)
    if not chunks or chunks[0][0] != b"HEAD":
        raise UimError("missing HEAD chunk")
    head = chunks[0][1]
    version = (head[0], head[1], head[2])
    version_str = ".".join(map(str, version))

    if version == (3, 0, 0):
        data_body = next((b for cid, b in chunks if cid == b"DATA"), None)
        if data_body is None:
            raise UimError("UIM 3.0.0: DATA chunk missing")
        ink_object = parse_message(data_body)
        sensors = _parse_sensor_data(_msg(ink_object, 1))
        raw = _strokes_300(_msg(ink_object, 2))
        _, raster = _brush_names(_msg(ink_object, 3))
        transform = _transform_300(ink_object)
        properties = {_str(p, 1): _str(p, 2) for p in _msgs(ink_object, 8)}
    elif version == (3, 1, 0):
        # HEAD: version(3) + reserved(1) + N * 8-byte chunk descriptions
        descriptions = [head[4 + i * 8:4 + i * 8 + 8]
                        for i in range((len(head) - 4) // 8)]
        sensors: dict[bytes, dict[str, list[float]]] = {}
        raw = []
        raster: set[str] = set()
        transform = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
        properties: dict[str, str] = {}
        for (cid, body), desc in zip(chunks[1:], descriptions):
            content_type = desc[3] if len(desc) >= 5 else 1
            if content_type != 1:  # only protobuf payloads supported
                _logger.warning("uim: skipping %r chunk with content type %d",
                                cid, content_type)
                continue
            body = _decompress(body, desc[4] if len(desc) >= 5 else 0)
            if body is None:
                continue
            if cid == b"INPT":
                sensors = _parse_sensor_data(parse_message(body))
            elif cid == b"INKD":
                ink_data = parse_message(body)
                raw = _strokes_310(ink_data)
                transform = _transform_310(ink_data)
            elif cid == b"BRSH":
                _, raster = _brush_names(parse_message(body))
            elif cid == b"PRPS":
                properties = {_str(p, 1): _str(p, 2)
                              for p in _msgs(parse_message(body), 1)}
            # KNWG (semantic triples) and INKS (tree) intentionally skipped
    else:
        raise UimError(f"unsupported UIM version {version_str}")

    strokes = [s for s in (_ir_stroke(r, sensors, raster, transform)
                           for r in raw) if s is not None]
    return _build_document(strokes, properties, version_str, title)


class UimReader:
    format_id = FORMAT_ID
    extensions = (".uim",)

    def detect(self, path: Path) -> bool:
        try:
            with open(path, "rb") as f:
                head = f.read(12)
            return head[:4] == b"RIFF" and head[8:12] == b"UINK"
        except OSError:
            return False

    def read(self, path: Path) -> ir.Document:
        return read_uim(path.read_bytes(), title=path.stem)
