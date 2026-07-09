"""Samsung Notes (.sdocx) -> IR.

Format facts learned from squ1dd13/sdocx2pdf (MIT,
https://github.com/squ1dd13/sdocx2pdf) — an independent Python
implementation of the documented layout; no code was ported. Facts
below are [verified] against that reference and the public corpus
samples unless marked otherwise; no first-party (self-made) corpus
exists yet, so [verified]-against-the-app upgrades are deferred until
Samsung hardware is available (docs/formats/sdocx.md).

Container: a zip holding
  pageIdInfo.dat      sha256 of note.note + ordered page uuid+hash list
  <uuid>.page         one binary member per page (S-Pen SDK object tree)
  note.note           document header incl. the string registry that
                      pen names / advanced settings are interned into
  media/mediaInfo.dat file registry for media/* (images, PDFs, .spi)
  end_tag.bin         document metadata, ends "Document for S-Pen SDK"

All integers little-endian. Recurring primitives:
  bitfield       u8 byte-count (0-4) + that many bytes
  short string   u16 char-count + UTF-16-LE chars (u8 variant for uuids)
  timestamp      i64 microseconds since epoch
  framed record  u32 size (inclusive of the size field itself) + payload;
                 "flex offsets" inside frames count from the size field

A .page = header (page_end_offset u32, flex_offset u32, flags, then
orientation/width/height/offset x/y u32s, uuid, mtime, versions) ...
then at page_end_offset: u16 layer count, u16 current layer, layers,
32-byte hash, literal "Page for SAMSUNG S-Pen SDK". Each layer = framed
header + u32 object count + objects + 32-byte hash. Each object =
u8 type, u16 child count, u32 size, payload ending in a 32-byte
sha256(uuid + mtime_micros). Object type 1 = stroke: an ObjectBase
frame (data type 0) then a stroke frame (data type 1) holding
  u16 event count, events, u16 tool type, then flag-gated fields
  (colour BGRA bytes, pen size f32, string-registry ids for pen name /
  advanced settings, fixed width, dash type, ...).
Events come uncompressed (x,y f64 pairs; pressures f32; timestamps u32;
optional tilt+orientation f32s) or delta-compressed when the stroke's
"curve" property bit is set: full first event, then per-event u16
fixed-point deltas (points: sign+10.5 bits; pressure/tilt: sign+3.12
bits; timestamps: plain u16). Compressed point deltas are applied
x-then-y in file order [verified against the reference implementation's
behaviour; its variable naming disagrees with itself].

Units: page width/height are abstract canvas units; Samsung's own PDF
export maps the page's short edge to the A4 short edge (210mm), so
point_scale = 595.276 / min(w, h) [inferred]. y grows downward, origin
top-left [verified]. Pressure is 0-1 [verified]. Tilt is radians, 0 =
perpendicular to the page; orientation is radians, 0 = tip toward page
top, +pi/2 = tip toward right [verified, Android axis convention].

Rendering approximation carried into WIDTH/appearance: the reference
renders stroke radius = 0.5 * pen_size * clamp(pressure, 0.4, 0.7)
(constant 0.45 pressure for non-pressure tools) and 2.5x the pen size
for highlighter-family tools [inferred — matches app output visually,
not byte-verified]. Raw PRESSURE/TILT/TIMESTAMP channels are always
preserved so `--fidelity raw` is faithful regardless.

Not parsed (skipped by size): text boxes, images, shapes, PDF
annotations' background PDFs, audio, and the hash chain (we read the
hashes but do not verify them).
"""
from __future__ import annotations

import logging
import math
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .. import ir

_logger = logging.getLogger(__name__)

FORMAT_ID = "sdocx"

A4_WIDTH_PT = 595.276  # 210 mm

PAGE_END_STRING = b"Page for SAMSUNG S-Pen SDK"
END_TAG_IDENT = b"Document for S-Pen SDK"

PEN_PREFIX = "com.samsung.android.sdk.pen.pen.preload."

#: Samsung preload pen class -> neutral family. [verified] name set from
#: the reference; family assignment is our judgement.
TOOL_FAMILY = {
    "FountainPen": ir.ToolFamily.PEN,
    "ObliquePen": ir.ToolFamily.CALLIGRAPHY,
    "InkPen2": ir.ToolFamily.BALLPOINT,
    "Pencil2": ir.ToolFamily.PENCIL,
    "BrushPen": ir.ToolFamily.BRUSH,
    "Marker4": ir.ToolFamily.HIGHLIGHTER,
    "StraightHighlighter": ir.ToolFamily.HIGHLIGHTER,
    "Marker3": ir.ToolFamily.MARKER,
    "StraightMarker": ir.ToolFamily.MARKER,
}

HIGHLIGHTER_FAMILIES = (ir.ToolFamily.HIGHLIGHTER, ir.ToolFamily.MARKER)


class SdocxError(ValueError):
    pass


class _S:
    """Little-endian cursor over bytes."""

    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise SdocxError(
                f"short read: need {n} bytes at {self.pos}, "
                f"have {len(self.data) - self.pos}")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def _unpack(self, fmt: str, size: int):
        return struct.unpack(fmt, self.take(size))[0]

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return self._unpack("<H", 2)

    def i32(self) -> int:
        return self._unpack("<i", 4)

    def u32(self) -> int:
        return self._unpack("<I", 4)

    def i64(self) -> int:
        return self._unpack("<q", 8)

    def f32(self) -> float:
        return self._unpack("<f", 4)

    def f64(self) -> float:
        return self._unpack("<d", 8)

    def u16s(self, n: int) -> tuple:
        return struct.unpack(f"<{n}H", self.take(2 * n))

    def u32s(self, n: int) -> tuple:
        return struct.unpack(f"<{n}I", self.take(4 * n))

    def f32s(self, n: int) -> tuple:
        return struct.unpack(f"<{n}f", self.take(4 * n))

    def f64s(self, n: int) -> tuple:
        return struct.unpack(f"<{n}d", self.take(8 * n))

    def bitfield(self) -> int:
        """u8 byte count (0-4) then that many little-endian bytes."""
        n = self.u8()
        if n > 4:
            raise SdocxError(f"bitfield size {n} > 4 at {self.pos - 1}")
        return int.from_bytes(self.take(n), "little")

    def short_u16_str(self) -> str:
        n = self.u16()
        return self.take(2 * n).decode("utf-16-le")

    def short_u8_str(self) -> str:
        n = self.u16()
        return self.take(n).decode("utf-8")


# --------------------------------------------------------------- fixed point

def fixed_point_delta(v: int) -> float:
    """16-bit point-component delta: sign bit, 10-bit int, 5-bit fraction."""
    a = ((v & 0x7FFF) >> 5) + (v & 0x1F) / 32.0
    return -a if v & 0x8000 else a


def fixed_small_delta(v: int) -> float:
    """16-bit pressure/tilt delta: sign bit, 3-bit int, 12-bit fraction."""
    a = ((v & 0x7FFF) >> 12) + (v & 0xFFF) / 4096.0
    return -a if v & 0x8000 else a


# ------------------------------------------------------------- stroke events

@dataclass
class RawStroke:
    """Decoded stroke object, pre-IR."""

    x: list[float] = field(default_factory=list)
    y: list[float] = field(default_factory=list)
    pressure: list[float] = field(default_factory=list)
    timestamp: list[int] = field(default_factory=list)
    tilt: list[float] | None = None
    orientation: list[float] | None = None
    tool_type: int = 0
    pen_name: str = ""
    color_bgra: bytes = b"\x00\x00\x00\xff"
    pen_size: float = 1.0
    fixed_width: bool = False
    fixed_opacity: bool = False
    eraser: bool = False
    millisecond_mode: bool = False
    advanced_settings: str = ""


def _parse_events(s: _S, count: int, compressed: bool,
                  has_tilt: bool, rs: RawStroke) -> None:
    if has_tilt:
        rs.tilt, rs.orientation = [], []
    if count == 0:
        return
    if not compressed:
        pts = s.f64s(2 * count)
        rs.x = list(pts[0::2])
        rs.y = list(pts[1::2])
        rs.pressure = list(s.f32s(count))
        rs.timestamp = list(s.u32s(count))
        if has_tilt:
            rs.tilt = list(s.f32s(count))
            rs.orientation = list(s.f32s(count))
        return

    # Delta-compressed: full first event, u16 deltas for the rest.
    n = count - 1
    x, y = s.f64(), s.f64()
    dxy = s.u16s(2 * n)
    p = s.f32()
    dp = s.u16s(n)
    t = s.u32()
    dt = s.u16s(n)
    if has_tilt:
        tilt = s.f32()
        dtilt = s.u16s(n)
        ori = s.f32()
        dori = s.u16s(n)
    rs.x.append(x)
    rs.y.append(y)
    rs.pressure.append(p)
    rs.timestamp.append(t)
    if has_tilt:
        rs.tilt.append(tilt)
        rs.orientation.append(ori)
    for i in range(n):
        x += fixed_point_delta(dxy[2 * i])
        y += fixed_point_delta(dxy[2 * i + 1])
        p += fixed_small_delta(dp[i])
        t += dt[i]
        rs.x.append(x)
        rs.y.append(y)
        rs.pressure.append(p)
        rs.timestamp.append(t)
        if has_tilt:
            tilt += fixed_small_delta(dtilt[i])
            ori += fixed_small_delta(dori[i])
            rs.tilt.append(tilt)
            rs.orientation.append(ori)


# ------------------------------------------------------------- object frames

def _frame(s: _S, expect_type: int | None = None) -> tuple[_S, int, int, int]:
    """Read a framed record header.

    Returns (window over the whole frame incl. the size field, flex
    offset [frame-relative], property bits, field bits) and advances the
    outer cursor past the frame.
    """
    start = s.pos
    size = s.u32()
    if size < 4 or start + size > len(s.data):
        raise SdocxError(f"bad frame size {size} at {start}")
    w = _S(s.data[start:start + size], 4)
    s.pos = start + size
    if expect_type is not None:
        data_type = w.u16()
        if data_type != expect_type:
            raise SdocxError(
                f"expected frame data type {expect_type}, got {data_type}")
    flex = w.u32()
    props = w.bitfield()
    fields = w.bitfield()
    return w, flex, props, fields


def parse_stroke(payload: bytes, strings: dict[int, str]) -> RawStroke:
    """Parse one stroke-object payload (object hash already stripped)."""
    s = _S(payload)
    _frame(s, expect_type=0)  # ObjectBase: skipped wholesale

    w, flex, props, fields = _frame(s, expect_type=1)
    rs = RawStroke()
    compressed = bool(props & (1 << 0))
    has_tilt = bool(props & (1 << 2))
    rs.eraser = bool(props & (1 << 3))
    rs.fixed_width = bool(props & (1 << 4))
    rs.millisecond_mode = bool(props & (1 << 5))
    rs.fixed_opacity = bool(props & (1 << 11))

    count = w.u16()
    _parse_events(w, count, compressed, has_tilt, rs)
    rs.tool_type = w.u16()

    if flex == 0:
        return rs
    w.pos = flex
    # Flag-gated fields, in bit order. Unknown bits before/between known
    # ones would desynchronize everything after them -> hard error.
    known = 0b111111111110011110
    if fields & ~known:
        raise SdocxError(f"unknown stroke field bits 0x{fields & ~known:x}")
    if fields & (1 << 1):
        rs.advanced_settings = strings.get(w.u32(), "")
    if fields & (1 << 2):
        rs.color_bgra = w.take(4)
    if fields & (1 << 3):
        rs.pen_size = w.f32()
    if fields & (1 << 4):
        w.u32()  # unknown u32 (present in reference, meaning unknown)
    if fields & (1 << 7):
        rs.pen_name = strings.get(w.u32(), "")
    if fields & (1 << 8):
        w.f32()  # fixed width value
    if fields & (1 << 9):
        w.u32()  # size level
    if fields & (1 << 10):
        w.u32()  # particle density
    if fields & (1 << 11):
        w.u32()  # rendering level
    if fields & (1 << 12):
        w.u32()  # original width
    if fields & (1 << 13):
        w.f32()  # initial tolerance
    if fields & (1 << 14):
        w.u16()  # dash type
    if fields & (1 << 15):
        w.f32()  # dash offset
    if fields & (1 << 16):
        w.u16()  # stroke type
    if fields & (1 << 17):
        w.f32()  # pen repeat distance
    return rs


# --------------------------------------------------------------- page member

@dataclass
class RawLayer:
    name: str = ""
    visible: bool = True
    strokes: list[RawStroke] = field(default_factory=list)
    other_objects: int = 0


@dataclass
class RawPage:
    width: int = 0
    height: int = 0
    orientation: int = 0
    uuid: str = ""
    layers: list[RawLayer] = field(default_factory=list)


def _parse_layer(s: _S, strings: dict[int, str]) -> RawLayer:
    header_start = s.pos
    size = s.u32()
    if size < 4 or header_start + size > len(s.data):
        raise SdocxError(f"bad layer header size {size} at {header_start}")
    # Layer flex offset is absolute in the page stream (unlike object
    # frames); we don't need the flex fields except name/visibility.
    flex = s.u32()
    props = s.bitfield()
    fields = s.bitfield()
    s.u32()  # layer id
    layer = RawLayer(visible=not (props & 1))
    if flex:
        s.pos = flex
    if fields & (1 << 0):
        s.u8()  # alpha
    if fields & (1 << 1):
        s.take(4)  # background colour
    if fields & (1 << 2):
        layer.name = s.short_u16_str()
    # Remaining flex fields (uuid/mtime/thumbnail/shadow) are skipped by
    # jumping to the end of the header frame:
    s.pos = header_start + size

    n_objects = s.u32()
    for _ in range(n_objects):
        obj_type = s.u8()
        children = s.u16()
        if children:
            _logger.warning("sdocx: object with %d children (unsupported); "
                            "skipping by size", children)
        obj_size = s.u32()
        payload = s.take(obj_size)
        if obj_type == 1 and not children:
            # Last 32 bytes are the object hash.
            layer.strokes.append(parse_stroke(payload[:-32], strings))
        else:
            layer.other_objects += 1
    s.take(32)  # layer hash (unverified)
    return layer


def parse_page(data: bytes, strings: dict[int, str]) -> RawPage:
    s = _S(data)
    page_end = s.u32()
    s.u32()  # flex offset (page fields skipped via page_end)
    s.bitfield()  # property flags (bit 0: text-only)
    s.bitfield()  # field flags
    page = RawPage(orientation=s.u32(), width=s.u32(), height=s.u32())
    s.u32()  # offset x
    s.u32()  # offset y
    page.uuid = s.short_u16_str()

    # All flag-gated page fields (drawn rect, template, background, PDF
    # attachments, recognition data...) live before page_end: skip them.
    if not 0 < page_end <= len(data):
        raise SdocxError(f"bad page end offset {page_end}")
    s.pos = page_end
    n_layers = s.u16()
    s.u16()  # current layer index
    for _ in range(n_layers):
        page.layers.append(_parse_layer(s, strings))
    s.take(32)  # page hash (unverified; referenced by pageIdInfo.dat)
    if s.data[s.pos:] != PAGE_END_STRING:
        raise SdocxError("missing page end string")
    return page


# ------------------------------------------------------- container top level

def read_page_list(data: bytes) -> list[str]:
    """pageIdInfo.dat: note.note hash + ordered (uuid, page hash) list."""
    s = _S(data)
    s.take(32)  # note.note sha256 (unverified)
    return [(s.short_u16_str(), s.take(32))[0] for _ in range(s.u16())]


def read_note_strings(data: bytes) -> tuple[dict[int, str], int, int]:
    """note.note: pull the string registry + document width/height.

    Only the fields up to the registry (flag bit 10) are decoded; the
    title/body rich-text blobs are skipped as opaque bytes.
    """
    s = _S(data)
    flex = s.u32()  # relative to member start
    s.bitfield()  # property flags
    fields = s.bitfield()
    s.u32()  # format version
    s.short_u16_str()  # document id
    s.u32()  # file revision
    s.i64()  # created
    s.i64()  # modified
    width = s.u32()
    height = s.u32()

    if not (fields & (1 << 10)) or not 0 < flex <= len(data):
        return {}, width, height
    s.pos = flex
    # Walk flag-gated fields in bit order until the string registry.
    unskippable = (1 << 4) | (1 << 5) | (1 << 8)
    if fields & unskippable & ((1 << 10) - 1):
        raise SdocxError("unknown note.note field bits before registry")
    if fields & (1 << 0):
        s.short_u16_str()  # app name
    if fields & (1 << 1):
        s.u32(), s.u32(), s.short_u16_str()  # app version
    if fields & (1 << 2):
        s.short_u16_str(), s.short_u16_str(), s.short_u16_str(), s.u32()
    if fields & (1 << 3):
        s.f64(), s.f64()  # latitude/longitude
    if fields & (1 << 6):
        s.short_u16_str()  # template uri
    if fields & (1 << 7):
        s.u32()  # last edited page index
    if fields & (1 << 9):
        s.i32(), s.i64()  # last edited page image id + time
    # Bit 10: the registry itself. u32 byte size, then u16 count of
    # (u32 id, short string) entries.
    total = s.u32()
    if total == 0:
        return {}, width, height
    strings = {}
    for _ in range(s.u16()):
        key = s.u32()
        strings[key] = s.short_u16_str()
    return strings, width, height


def read_end_tag(data: bytes) -> dict:
    """end_tag.bin: document metadata. Fail-soft — only used for hints."""
    out: dict = {}
    try:
        s = _S(data)
        s.u16()  # size of the rest
        if not data.endswith(END_TAG_IDENT):
            return out
        s.u32()  # format version
        s.short_u16_str()  # note uuid
        s.i64()  # modified
        out["landscape"] = bool(s.u32() & (1 << 1))
        s.short_u16_str()  # cover image
        out["note_width"] = s.u32()
        out["note_height"] = s.f32()
        out["app_name"] = s.short_u16_str()
        major, minor = s.u32(), s.u32()
        out["app_version"] = f"{major}.{minor}.{s.short_u16_str()}"
        s.u32()  # min format version
        s.i64()  # created
        s.u32()  # last viewed page
        out["page_model"] = "pageless" if s.u16() == 1 else "paged"
    except (SdocxError, UnicodeDecodeError, struct.error):
        _logger.debug("sdocx: end_tag.bin parse failed", exc_info=True)
    return out


# ------------------------------------------------------------------ IR build

def _tool_family(rs: RawStroke) -> ir.ToolFamily:
    if rs.eraser or rs.tool_type == 4:
        return ir.ToolFamily.ERASER
    name = rs.pen_name
    if name.startswith(PEN_PREFIX):
        name = name[len(PEN_PREFIX):]
    return TOOL_FAMILY.get(name, ir.ToolFamily.UNKNOWN)


def _ir_stroke(rs: RawStroke) -> ir.Stroke:
    family = _tool_family(rs)
    b, g, r, a = rs.color_bgra
    color = ir.Color(r / 255.0, g / 255.0, b / 255.0)
    opacity = a / 255.0
    is_highlight = family in HIGHLIGHTER_FAMILIES

    n = len(rs.x)
    pressures = [min(max(p, 0.0), 1.0) for p in rs.pressure]
    channels: dict[ir.Channel, list[float]] = {
        ir.Channel.PRESSURE: pressures,
    }
    if rs.timestamp:
        # u32 event clock, assumed milliseconds [inferred].
        t0 = rs.timestamp[0]
        channels[ir.Channel.TIMESTAMP] = [
            (t - t0) / 1000.0 for t in rs.timestamp]
    if rs.tilt:
        # Samsung: 0 = perpendicular to page; IR altitude: pi/2 =
        # perpendicular. Orientation 0 = tip toward page top; IR
        # azimuth 0 = +x axis [inferred conversion].
        channels[ir.Channel.TILT_ALTITUDE] = [
            math.pi / 2 - t for t in rs.tilt]
        channels[ir.Channel.TILT_AZIMUTH] = [
            o - math.pi / 2 for o in rs.orientation]

    # Rendered-width model from the reference renderer [inferred]:
    # width = eff_size * clamp(pressure, 0.4, 0.7); constant 0.45
    # pressure for tools the app treats as non-pressure-sensitive.
    eff_size = rs.pen_size * (2.5 if is_highlight else 1.0)
    pressure_sensitive = (not rs.fixed_width and not is_highlight
                          and family is not ir.ToolFamily.CALLIGRAPHY
                          and not (family is ir.ToolFamily.PENCIL
                                   and rs.fixed_opacity))
    if pressure_sensitive and n:
        channels[ir.Channel.WIDTH] = [
            eff_size * min(max(p, 0.4), 0.7) for p in pressures]
        mode, width = ir.GeometryMode.STROKED_VARIABLE, None
    else:
        mode, width = ir.GeometryMode.STROKED_CONSTANT, eff_size * 0.45

    return ir.Stroke(
        x=list(rs.x), y=list(rs.y),
        tool=ir.ToolRef(
            family=family,
            native=ir.NativeTool(FORMAT_ID, rs.pen_name or rs.tool_type, {
                "pen_size": rs.pen_size,
                "tool_type": rs.tool_type,
                "fixed_width": rs.fixed_width,
                "fixed_opacity": rs.fixed_opacity,
                "advanced_settings": rs.advanced_settings,
            }),
        ),
        color=color,
        channels=channels,
        appearance=ir.StrokeAppearance(
            mode=mode,
            width=width,
            color=color,
            opacity=opacity,
            cap=ir.LineCap.ROUND,
            underlay=is_highlight,
            blend=(ir.BlendMode.DARKEN if is_highlight and opacity < 1.0
                   else ir.BlendMode.NORMAL),
        ),
    )


class SdocxReader:
    format_id = FORMAT_ID
    extensions = (".sdocx",)

    def detect(self, path: Path) -> bool:
        try:
            if not zipfile.is_zipfile(path):
                return False
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
            # pageIdInfo.dat + *.page discriminate from the other
            # zip-based note formats (goodnotes, notability, saber).
            return ("pageIdInfo.dat" in names
                    and any(n.endswith(".page") for n in names))
        except OSError:
            return False

    def read(self, path: Path) -> ir.Document:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            page_uuids = read_page_list(zf.read("pageIdInfo.dat"))
            strings: dict[int, str] = {}
            doc_w = doc_h = 0
            if "note.note" in names:
                try:
                    strings, doc_w, doc_h = read_note_strings(
                        zf.read("note.note"))
                except (SdocxError, UnicodeDecodeError) as e:
                    _logger.warning("sdocx: note.note parse failed (%s); "
                                    "pen names unavailable", e)
            meta = (read_end_tag(zf.read("end_tag.bin"))
                    if "end_tag.bin" in names else {})

            pages = []
            for uuid in page_uuids:
                raw = parse_page(zf.read(f"{uuid}.page"), strings)
                w = raw.width or doc_w or 1440
                h = raw.height or doc_h or 2038
                layers = [
                    ir.Layer(
                        strokes=[_ir_stroke(rs) for rs in lay.strokes],
                        name=lay.name,
                        visible=lay.visible,
                    )
                    for lay in raw.layers
                ]
                pages.append(ir.Page(
                    bounds=ir.Rect(0.0, 0.0, float(w), float(h)),
                    # Samsung's export maps the short edge to A4's 210mm
                    # [inferred].
                    point_scale=A4_WIDTH_PT / float(min(w, h)),
                    layers=layers,
                    extra={"sdocx_uuid": raw.uuid,
                           "sdocx_orientation": raw.orientation},
                ))

        return ir.Document(
            format_id=FORMAT_ID,
            title=path.stem,
            pages=pages,
            orientation="landscape" if meta.get("landscape") else "portrait",
            metadata={f"sdocx_{k}": v for k, v in meta.items()},
        )
