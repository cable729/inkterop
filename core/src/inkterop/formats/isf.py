"""Microsoft Ink Serialized Format (.isf) -> IR.

ISF is the Tablet PC / Windows Ink stroke container: Windows clipboard
ink, Microsoft Journal exports, and the ink streams embedded inside
OneNote .one files. Format facts come from two fully-permitted sources:

- the MS-ISF specification (Microsoft Open Specification Promise;
  learn.microsoft.com/en-us/uwp/specifications/ink-serialized-format)
- Microsoft's own ISF codec in WPF (github.com/dotnet/wpf, MIT;
  ``MS/internal/Ink/InkSerializedFormat/*``), which resolves everything
  the spec leaves ambiguous (algo-byte layout, bit order, defaults).

Container [verified against spec + WPF source]:

  mbuint version (must be 0), mbuint stream size, then tagged blocks.
  A tag is a multibyte-encoded uint; tags 0-30 are structural, tags
  50-87 name the predefined property GUIDs (base 50 + GUID index), and
  tags >= 100 index the custom-GUID table (tag - 100). Most blocks are
  length-prefixed; exceptions: ink-space rect (4 signed mbints) and the
  bare single-transform blocks (fixed float payloads).

Numbers [verified]: multibyte uints are 7-bit little-endian groups with
a continuation high bit (protobuf-varint compatible). Signed values are
sign-flipped: ``(abs(v) << 1) | sign`` -- NOT zigzag.

Packet (per-point) data [verified against WPF AlgoModule/HuffCodec/
GorillaCodec]: each property array starts with one algorithm byte:

  0x80 | i (i < 8)  "indexed Huffman": delta-delta transformed values,
                    Huffman-coded with table i of the 8 spec tables
                    (prefix = N one-bits + zero; then bits[N] payload
                    bits holding (value - mins[N]) << 1 | sign; prefix
                    of table-size ones = 64-bit escape). MSB-first bit
                    packing.
  0x00 | flags      "gorilla" bit-packing: low 5 bits = bits per value
                    (0 => 32), bit 0x20 = delta-delta (first two values
                    are then multibyte sign-encoded and the remaining
                    n-2 are bit-packed). Values are two's-complement in
                    the given bit width, MSB-first. Algo byte 0x00 ==
                    raw big-endian int32s.

Tables mapped to IR: stroke descriptor (which packet properties each
stroke carries; X+Y implicit), metric (per-property logical min/max/
unit/resolution), transform (applied to points; coordinates are
HIMETRIC, 1 unit = 0.01mm, so ``point_scale = 72/2540``), drawing
attributes (COLORREF, pen width/height in HIMETRIC, pen tip,
transparency byte, raster op -- MaskPen(9) marks highlighters
[verified: WPF DrawingAttributeSerializer]).

Channel normalization: NORMAL_PRESSURE via its metric entry (default
0..1023) -> 0-1; azimuth/altitude default 0.1-degree units ->
radians (azimuth mapped ``radians(90 - deg)`` [inferred: ISF azimuth is
clockwise-from-north]); TIMER_TICK assumed milliseconds [inferred].

Honest subset -- parsed and *skipped* with correct byte accounting:
stroke extended properties, point properties, button states, custom
global properties (kept raw in ``Document.metadata``), LZ-compressed
property payloads, pen-width mantissa refinements, CompressionHeader
custom Huffman codecs (packet arrays that reference codec indexes >= 8
raise). Divergences from WPF quirks are deliberate and documented in
docs/formats/isf.md.

The multibyte/sign-flip/delta-delta/Huffman/bit-packing primitives in
this module are the shared low layer a future OneNote reader imports.
"""
from __future__ import annotations

import logging
import math
import struct
from pathlib import Path

from .. import ir

_logger = logging.getLogger(__name__)

FORMAT_ID = "isf"

#: HIMETRIC unit (0.01 mm) -> PDF points. [verified: WPF uses 2540/96
#: HIMETRIC-per-DIP; 72pt/inch / 2540 himetric/inch]
POINT_SCALE = 72.0 / 2540.0


class IsfError(ValueError):
    pass


# ------------------------------------------------------------ multibyte ints

def read_mbuint(buf, pos: int) -> tuple[int, int]:
    """Multibyte uint: 7-bit little-endian groups, high continuation bit."""
    result = shift = 0
    while True:
        if pos >= len(buf):
            raise IsfError("multibyte uint past end of buffer")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise IsfError("multibyte uint too long")


def read_mbsint(buf, pos: int) -> tuple[int, int]:
    """Signed multibyte: sign-flip encoding ``(abs << 1) | sign``."""
    raw, pos = read_mbuint(buf, pos)
    value = raw >> 1
    return (-value if raw & 1 else value), pos


def write_mbuint(value: int) -> bytes:
    if value < 0:
        raise IsfError("multibyte uint cannot encode negatives")
    out = bytearray()
    while value > 0x7F:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    out.append(value)
    return bytes(out)


def write_mbsint(value: int) -> bytes:
    return write_mbuint((abs(value) << 1) | (1 if value < 0 else 0))


# ------------------------------------------------------------------ bit I/O
# ISF packs bits MSB-first within each byte [verified: WPF BitStream.cs].

class BitReader:
    __slots__ = ("buf", "start", "pos", "bit")

    def __init__(self, buf, pos: int = 0):
        self.buf = buf
        self.start = pos
        self.pos = pos
        self.bit = 0

    def read_bit(self) -> int:
        if self.pos >= len(self.buf):
            raise IsfError("bit read past end of buffer")
        value = (self.buf[self.pos] >> (7 - self.bit)) & 1
        self.bit += 1
        if self.bit == 8:
            self.bit = 0
            self.pos += 1
        return value

    def read(self, count: int) -> int:
        value = 0
        for _ in range(count):
            value = (value << 1) | self.read_bit()
        return value

    @property
    def bytes_consumed(self) -> int:
        """Bytes touched so far (a partially-read byte counts)."""
        return (self.pos - self.start) + (1 if self.bit else 0)


class BitWriter:
    __slots__ = ("out", "bit")

    def __init__(self):
        self.out = bytearray()
        self.bit = 0

    def write(self, value: int, count: int) -> None:
        for i in range(count - 1, -1, -1):
            if self.bit == 0:
                self.out.append(0)
            if (value >> i) & 1:
                self.out[-1] |= 1 << (7 - self.bit)
            self.bit = (self.bit + 1) % 8

    def getvalue(self) -> bytes:
        return bytes(self.out)


# --------------------------------------------------------------- delta-delta

class DeltaDelta:
    """The spec's second-derivative transform (WPF ``DeltaDelta``).

    forward:  dd_i = v_i + v_{i-2} - 2*v_{i-1}   (state starts at 0, so
    dd_0 = v_0 and dd_1 = v_1 - 2*v_0). Values whose |dd| exceeds int32
    split into (low 32 bits, extra = high bits << 1 | sign).
    """

    __slots__ = ("d1", "d2")

    def __init__(self):
        self.d1 = 0
        self.d2 = 0

    def transform(self, value: int) -> tuple[int, int]:
        dd = value + self.d2 - 2 * self.d1
        self.d2 = self.d1
        self.d1 = value
        if abs(dd) <= 0x7FFFFFFF:
            return dd, 0
        magnitude = abs(dd)
        extra = ((magnitude >> 32) << 1) | (1 if dd < 0 else 0)
        return magnitude & 0xFFFFFFFF, extra

    def inverse(self, xf: int, extra: int = 0) -> int:
        if extra:
            magnitude = ((extra >> 1) << 32) | (xf & 0xFFFFFFFF)
            dd = -magnitude if extra & 1 else magnitude
        else:
            dd = xf
        value = dd + 2 * self.d1 - self.d2
        self.d2 = self.d1
        self.d1 = value
        return value


# -------------------------------------------------------------- Huffman codec
# The 8 default bit-allocation tables. [verified: identical in the spec
# appendix (DEF_BAA_DATA) and WPF HuffCodec.DefaultBAAData]

HUFFMAN_BAA_DATA: tuple[tuple[int, ...], ...] = (
    (0, 1, 2, 4, 6, 8, 12, 16, 24, 32),
    (0, 1, 1, 2, 4, 8, 12, 16, 24, 32),
    (0, 1, 1, 1, 2, 4, 8, 14, 22, 32),
    (0, 2, 2, 3, 5, 8, 12, 16, 24, 32),
    (0, 3, 4, 5, 8, 12, 16, 24, 32),
    (0, 4, 6, 8, 12, 16, 24, 32),
    (0, 6, 8, 12, 16, 24, 32),
    (0, 7, 8, 12, 16, 24, 32),
)


class HuffmanCodec:
    """One indexed-Huffman table: prefix-length coded magnitude buckets."""

    def __init__(self, index: int):
        if not 0 <= index < len(HUFFMAN_BAA_DATA):
            raise IsfError(f"huffman table index {index} out of range "
                           "(custom codecs via TAG_COMPRESSION_HEADER are "
                           "not supported)")
        self.index = index
        self.bits = HUFFMAN_BAA_DATA[index]
        self.mins = [0] * len(self.bits)
        lower = 1
        for n in range(1, len(self.bits)):
            self.mins[n] = lower
            lower += 1 << (self.bits[n] - 1)

    def decode_one(self, reader: BitReader) -> tuple[int, int]:
        """-> (value, extra); extra != 0 only for the 64-bit escape."""
        prefix = 0
        while reader.read_bit():
            prefix += 1
        if prefix == 0:
            return 0, 0
        size = len(self.bits)
        if prefix < size:
            data_len = self.bits[prefix]
            raw = reader.read(data_len)
            negative = raw & 1
            value = (raw >> 1) + self.mins[prefix]
            return (-value if negative else value), 0
        if prefix == size:  # escape: extra (high bits+sign) then low data
            extra, _ = self.decode_one(reader)
            data, _ = self.decode_one(reader)
            return data, extra
        raise IsfError("invalid Huffman prefix")

    def encode_one(self, value: int, extra: int, writer: BitWriter) -> None:
        """Port of the spec's Encode() (also used by the fixture encoder)."""
        if value == 0:
            writer.write(0, 1)
            return
        size = len(self.bits)
        if extra:
            writer.write((1 << (size + 1)) - 2, size + 1)
            self.encode_one(extra, 0, writer)
            self.encode_one(value, 0, writer)
            return
        magnitude = abs(value)
        prefix_len = 1
        while prefix_len < size and magnitude >= self.mins[prefix_len]:
            prefix_len += 1
        data_len = self.bits[prefix_len - 1]
        writer.write((1 << prefix_len) - 2, prefix_len)
        packed = ((((magnitude - self.mins[prefix_len - 1])
                    & ((1 << (data_len - 1)) - 1)) << 1)
                  | (1 if value < 0 else 0))
        writer.write(packed, data_len)


# ------------------------------------------------------- packet decompression

def _decode_huffman_packets(buf, pos: int, count: int,
                            table_index: int) -> tuple[list[int], int]:
    """Indexed Huffman is always delta-delta transformed [verified: WPF
    HuffModule.FindDtXf unconditionally returns the DeltaDelta xform]."""
    codec = HuffmanCodec(table_index)
    dd = DeltaDelta()
    reader = BitReader(buf, pos)
    out: list[int] = []
    for _ in range(count):
        data, extra = codec.decode_one(reader)
        out.append(dd.inverse(data, extra))
    return out, pos + reader.bytes_consumed


def _decode_bitpacked_packets(buf, pos: int, count: int,
                              algo: int) -> tuple[list[int], int]:
    deldel = bool(algo & 0x20)
    bit_count = (algo & 0x1F) or 32
    out: list[int] = []
    dd = DeltaDelta() if deldel else None
    remaining = count
    if deldel:
        # first two values are multibyte sign-encoded delta-deltas
        for _ in range(min(2, count)):
            v, pos = read_mbsint(buf, pos)
            out.append(dd.inverse(v))
            remaining -= 1
    if remaining > 0:
        reader = BitReader(buf, pos)
        sign_bit = 1 << (bit_count - 1)
        for _ in range(remaining):
            v = reader.read(bit_count)
            if v & sign_bit:  # two's-complement sign extension
                v -= 1 << bit_count
            out.append(dd.inverse(v) if dd else v)
        pos += (remaining * bit_count + 7) >> 3
    return out, pos


def decode_packet_array(buf, pos: int, count: int) -> tuple[list[int], int]:
    """One per-point property array: algo byte + compressed values."""
    if count == 0:
        return [], pos
    if pos >= len(buf):
        raise IsfError("packet array starts past end of stroke block")
    algo = buf[pos]
    pos += 1
    kind = algo & 0xC0
    if kind == 0x80:
        return _decode_huffman_packets(buf, pos, count, algo & 0x1F)
    if kind == 0x00:
        return _decode_bitpacked_packets(buf, pos, count, algo)
    raise IsfError(f"unsupported packet compression byte 0x{algo:02x}")


# ------------------------------------------------------ property decompression
# cBits/cPads lookup for PROPERTY_BIT_PACK [verified: spec appendix table,
# identical to WPF GorillaCodec._gorIndexMap]

GORILLA_INDEX_TABLE: tuple[tuple[int, int], ...] = (
    (8, 0),
    (1, 0), (1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7),
    (2, 0), (2, 1), (2, 2), (2, 3),
    (3, 0), (3, 1), (3, 2),
    (4, 0), (4, 1),
    (5, 0), (5, 1),
    (6, 0), (6, 1),
    (7, 0), (7, 1),
)


def decompress_property(data: bytes) -> bytes:
    """Property-data (byte-array) decompression: the three bit-packing
    layouts. LZ (algo bit 0x80) is unsupported and raises."""
    if not data:
        raise IsfError("empty property payload")
    algo = data[0]
    if algo & 0x80:
        raise IsfError("LZ property compression not supported")
    if algo & 0x40:
        per_item, index = 4, algo & 0x3F
    elif algo & 0x20:
        per_item, index = 2, algo & 0x1F
    else:
        per_item, index = 1, algo & 0x1F
    if index < len(GORILLA_INDEX_TABLE):
        bit_count, pad_count = GORILLA_INDEX_TABLE[index]
    else:
        bit_count, pad_count = index - 16, 0
    if bit_count <= 0:
        raise IsfError(f"invalid property algo byte 0x{algo:02x}")
    units = ((len(data) - 1) * 8) // bit_count - pad_count
    reader = BitReader(data, 1)
    out = bytearray()
    sign_bit = 1 << (bit_count - 1)
    for _ in range(units):
        v = reader.read(bit_count)
        if per_item == 4 and v & sign_bit:  # ints are sign-extended
            v -= 1 << bit_count
        out += v.to_bytes(per_item, "little", signed=per_item == 4)
    return bytes(out)


# ------------------------------------------------------------------ tag layer

TAG_INK_SPACE_RECT = 0
TAG_GUID_TABLE = 1
TAG_DRAW_ATTRS_TABLE = 2
TAG_DRAW_ATTRS_BLOCK = 3
TAG_STROKE_DESC_TABLE = 4
TAG_STROKE_DESC_BLOCK = 5
TAG_BUTTONS = 6
TAG_NO_X = 7
TAG_NO_Y = 8
TAG_DIDX = 9
TAG_STROKE = 10
TAG_STROKE_PROPERTY_LIST = 11
TAG_POINT_PROPERTY = 12
TAG_SIDX = 13
TAG_COMPRESSION_HEADER = 14
TAG_TRANSFORM_TABLE = 15
TAG_TRANSFORM = 16
TAG_TRANSFORM_ISOTROPIC_SCALE = 17
TAG_TRANSFORM_ANISOTROPIC_SCALE = 18
TAG_TRANSFORM_ROTATE = 19
TAG_TRANSFORM_TRANSLATE = 20
TAG_TRANSFORM_SCALE_AND_TRANSLATE = 21
TAG_TRANSFORM_QUAD = 22
TAG_TIDX = 23
TAG_METRIC_TABLE = 24
TAG_METRIC_BLOCK = 25
TAG_MIDX = 26
TAG_MANTISSA = 27
TAG_PERSISTENT_FORMAT = 28
TAG_HIMETRIC_SIZE = 29
TAG_STROKE_IDS = 30
TAG_EXTENDED_TRANSFORM_TABLE = 31

#: Predefined property GUID tags = KNOWN_TAG_BASE + GUID index.
KNOWN_TAG_BASE = 50
CUSTOM_TAG_BASE = 100

TAG_X = 50
TAG_Y = 51
TAG_Z = 52
TAG_PACKET_STATUS = 53
TAG_TIMER_TICK = 54
TAG_SERIAL_NUMBER = 55
TAG_NORMAL_PRESSURE = 56
TAG_TANGENT_PRESSURE = 57
TAG_BUTTON_PRESSURE = 58
TAG_X_TILT = 59
TAG_Y_TILT = 60
TAG_AZIMUTH = 61
TAG_ALTITUDE = 62
TAG_TWIST = 63
TAG_PEN_STYLE = 67
TAG_COLORREF = 68
TAG_PEN_WIDTH = 69
TAG_PEN_HEIGHT = 70
TAG_PEN_TIP = 71
TAG_DRAWING_FLAGS = 72
TAG_TRANSPARENCY = 80
TAG_CURVE_FITTING_ERROR = 81
TAG_ROP = 87

GUID_PROPERTY_NAMES = {
    0: "X", 1: "Y", 2: "Z", 3: "PACKET_STATUS", 4: "TIMER_TICK",
    5: "SERIAL_NUMBER", 6: "NORMAL_PRESSURE", 7: "TANGENT_PRESSURE",
    8: "BUTTON_PRESSURE", 9: "X_TILT_ORIENTATION", 10: "Y_TILT_ORIENTATION",
    11: "AZIMUTH_ORIENTATION", 12: "ALTITUDE_ORIENTATION",
    13: "TWIST_ORIENTATION", 14: "PITCH_ROTATION", 15: "ROLL_ROTATION",
    16: "YAW_ROTATION", 17: "PEN_STYLE", 18: "COLORREF", 19: "PEN_WIDTH",
    20: "PEN_HEIGHT", 21: "PEN_TIP", 22: "DRAWING_FLAGS", 23: "CURSORID",
    24: "WORD_ALTERNATES", 25: "CHAR_ALTERNATES", 26: "INKMETRICS",
    27: "GUIDE_STRUCTURE", 28: "TIME_STAMP", 29: "LANGUAGE",
    30: "TRANSPARENCY", 31: "CURVE_FITTING_ERROR", 32: "RECO_LATTICE",
    33: "CURSORDOWN", 34: "SECONDARYTIPSWITCH", 35: "BARRELDOWN",
    36: "TABLETPICK", 37: "ROP",
}

#: Fixed payload byte sizes for the predefined property GUIDs; 0 = the
#: payload is size-prefixed. [verified: WPF KnownIdCache
#: OriginalISFIdPersistenceSize]
PROPERTY_DATA_SIZES = (
    4, 4, 4, 4, 8, 4, 2, 2, 2, 4, 4, 4, 4, 4, 2, 2, 2, 2, 4, 4,
    4, 1, 4, 4, 0, 0, 20, 12, 16, 2, 1, 4, 0, 4, 4, 4, 4, 4,
)

#: Default (min, max, resolution) per packet-property tag when no metric
#: block overrides them. [verified: WPF StylusPointPropertyInfoDefaults;
#: matches the spec's default packet description table]
DEFAULT_METRICS: dict[int, tuple[int, int, float]] = {
    TAG_NORMAL_PRESSURE: (0, 1023, 1.0),
    TAG_TANGENT_PRESSURE: (0, 1023, 1.0),
    TAG_BUTTON_PRESSURE: (0, 1023, 1.0),
    TAG_X_TILT: (0, 3600, 10.0),
    TAG_Y_TILT: (0, 3600, 10.0),
    TAG_AZIMUTH: (0, 3600, 10.0),
    TAG_ALTITUDE: (-900, 900, 10.0),
    TAG_TWIST: (0, 3600, 10.0),
}

_RASTER_OP_MASK_PEN = 9  # highlighter [verified: WPF]
_RASTER_OP_COPY_PEN = 13  # v1 default
_DEFAULT_PEN_WIDTH_HIMETRIC = 53.0  # v2 default (0.53mm) [inferred]
_V1_PEN_WIDTH_WHEN_MISSING = 25.0  # [verified: WPF DA serializer]

_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)  # m11, m12, m21, m22, dx, dy


# -------------------------------------------------------------- table parsing

class _DrawAttrs:
    __slots__ = ("color", "transparency", "width", "height", "pen_tip",
                 "raster_op", "flags", "pen_style", "extra")

    def __init__(self):
        self.color = (0, 0, 0)  # r, g, b bytes; v1 default black
        self.transparency = 0
        self.width: float | None = None
        self.height: float | None = None
        self.pen_tip = 0  # 0 ball/circle, 1 rectangle
        self.raster_op = _RASTER_OP_COPY_PEN
        self.flags = 0
        self.pen_style = 0
        self.extra: dict[str, str] = {}  # unknown property tag -> hex payload


def _skip_property_payload(block: bytes, pos: int, tag: int) -> tuple[bytes, int]:
    """Skip (and return raw) one predefined/custom property payload.
    Fixed-size for known GUIDs; else mbuint size + (size+1) bytes where
    the +1 is the compression algo byte [verified: WPF
    ExtendedPropertySerializer.DecodeAsISF]."""
    index = tag - KNOWN_TAG_BASE
    if 0 <= index < len(PROPERTY_DATA_SIZES) and PROPERTY_DATA_SIZES[index]:
        size = PROPERTY_DATA_SIZES[index]
        if pos + size > len(block):
            raise IsfError("property payload past end of block")
        return block[pos:pos + size], pos + size
    size, pos = read_mbuint(block, pos)
    size += 1  # algo byte
    if pos + size > len(block):
        raise IsfError("property payload past end of block")
    return block[pos:pos + size], pos + size


def _parse_draw_attrs_block(block: bytes) -> _DrawAttrs:
    da = _DrawAttrs()
    pos = 0
    while pos < len(block):
        tag, pos = read_mbuint(block, pos)
        if tag in (TAG_PEN_TIP, TAG_PEN_STYLE, TAG_DRAWING_FLAGS,
                   TAG_TRANSPARENCY, TAG_COLORREF, TAG_CURVE_FITTING_ERROR):
            value, pos = read_mbuint(block, pos)
            if tag == TAG_PEN_TIP:
                da.pen_tip = value
            elif tag == TAG_PEN_STYLE:
                da.pen_style = value
            elif tag == TAG_DRAWING_FLAGS:
                da.flags = value
            elif tag == TAG_TRANSPARENCY:
                da.transparency = min(value, 255)
            elif tag == TAG_COLORREF:  # 0x00BBGGRR
                da.color = (value & 0xFF, (value >> 8) & 0xFF,
                            (value >> 16) & 0xFF)
        elif tag == TAG_ROP:
            # 4 raw bytes; first is the raster-op value [verified: WPF]
            if pos + 4 > len(block):
                raise IsfError("truncated raster-op drawing attribute")
            da.raster_op = block[pos]
            pos += 4
        elif tag in (TAG_PEN_WIDTH, TAG_PEN_HEIGHT):
            value, pos = read_mbuint(block, pos)
            # optional TAG_MANTISSA fraction refinement; skipped (adds
            # < 0.001 himetric) but must be consumed for framing
            if pos < len(block):
                peek, after = read_mbuint(block, pos)
                if peek == TAG_MANTISSA:
                    msize, after = read_mbuint(block, after)
                    pos = after + msize + 1  # +1 algo byte
            if tag == TAG_PEN_WIDTH:
                da.width = float(value)
            else:
                da.height = float(value)
        else:
            payload, pos = _skip_property_payload(block, pos, tag)
            da.extra[str(tag)] = payload.hex()
    return da


class _StrokeDesc:
    __slots__ = ("packet_tags", "button_count", "stroke_prop_tags")

    def __init__(self, packet_tags: list[int], button_count: int = 0,
                 stroke_prop_tags: list[int] | None = None):
        self.packet_tags = packet_tags  # in stream order, X/Y included
        self.button_count = button_count
        self.stroke_prop_tags = stroke_prop_tags or []


_DEFAULT_DESC = _StrokeDesc([TAG_X, TAG_Y])


def _parse_stroke_descriptor(block: bytes) -> _StrokeDesc:
    no_x = no_y = False
    extra_tags: list[int] = []
    button_count = 0
    prop_tags: list[int] = []
    pos = 0
    while pos < len(block):
        tag, pos = read_mbuint(block, pos)
        if tag == TAG_NO_X:
            no_x = True
        elif tag == TAG_NO_Y:
            no_y = True
        elif tag == TAG_BUTTONS:
            button_count, pos = read_mbuint(block, pos)
            for _ in range(button_count):  # button GUID tags, unused
                if pos >= len(block):
                    break
                _tag, pos = read_mbuint(block, pos)
        elif tag == TAG_STROKE_PROPERTY_LIST:
            while pos < len(block):
                t, pos = read_mbuint(block, pos)
                prop_tags.append(t)
        else:
            extra_tags.append(tag)
    packet = ([] if no_x else [TAG_X]) + ([] if no_y else [TAG_Y]) + extra_tags
    return _StrokeDesc(packet, button_count, prop_tags)


def _parse_metric_block(block: bytes) -> dict[int, tuple[int, int, float]]:
    """-> {property tag: (min, max, resolution)}. Entry layout: signed
    min, signed max, mbuint unit, raw float32 resolution -- each field
    optional, bounded by the entry size [verified: spec fig. 9 + WPF]."""
    metrics: dict[int, tuple[int, int, float]] = {}
    pos = 0
    while pos < len(block):
        tag, pos = read_mbuint(block, pos)
        size, pos = read_mbuint(block, pos)
        entry_end = pos + size
        if entry_end > len(block):
            raise IsfError("metric entry past end of block")
        vmin, vmax, resolution = DEFAULT_METRICS.get(
            tag, (-(2 ** 31), 2 ** 31 - 1, 1.0))
        if pos < entry_end:
            vmin, pos = read_mbsint(block, pos)
        if pos < entry_end:
            vmax, pos = read_mbsint(block, pos)
        if pos < entry_end:
            _unit, pos = read_mbuint(block, pos)
        if pos + 4 <= entry_end:
            resolution = struct.unpack_from("<f", block, pos)[0]
        pos = entry_end
        metrics[tag] = (vmin, vmax, resolution if resolution else 1.0)
    return metrics


_TRANSFORM_FLOAT_COUNT = {
    TAG_TRANSFORM: 6,
    TAG_TRANSFORM_ISOTROPIC_SCALE: 1,
    TAG_TRANSFORM_ANISOTROPIC_SCALE: 2,
    TAG_TRANSFORM_TRANSLATE: 2,
    TAG_TRANSFORM_SCALE_AND_TRANSLATE: 4,
}


def _parse_transform_block(buf, pos: int, tag: int,
                           doubles: bool = False) -> tuple[tuple, int]:
    """-> ((m11, m12, m21, m22, dx, dy), new pos). Point mapping is
    row-vector style: x' = m11*x + m21*y + dx; y' = m12*x + m22*y + dy."""
    if tag == TAG_TRANSFORM_ROTATE:
        centidegrees, pos = read_mbuint(buf, pos)
        a = math.radians(centidegrees / 100.0)
        # proper rotation; WPF has a -cos quirk here that we do not copy
        return (math.cos(a), math.sin(a), -math.sin(a), math.cos(a),
                0.0, 0.0), pos
    count = _TRANSFORM_FLOAT_COUNT.get(tag)
    if count is None:
        raise IsfError(f"unsupported transform tag {tag}")
    fmt = f"<{count}{'d' if doubles else 'f'}"
    if pos + struct.calcsize(fmt) > len(buf):
        raise IsfError("truncated transform block")
    values = struct.unpack_from(fmt, buf, pos)
    pos += struct.calcsize(fmt)
    if tag == TAG_TRANSFORM:
        m11, m12, m21, m22, dx, dy = values
    elif tag == TAG_TRANSFORM_ISOTROPIC_SCALE:
        m11 = m22 = values[0]
        m12 = m21 = dx = dy = 0.0
    elif tag == TAG_TRANSFORM_ANISOTROPIC_SCALE:
        m11, m22 = values
        m12 = m21 = dx = dy = 0.0
    elif tag == TAG_TRANSFORM_TRANSLATE:
        dx, dy = values
        m11 = m22 = 1.0
        m12 = m21 = 0.0
    else:  # scale and translate
        m11, m22, dx, dy = values
        m12 = m21 = 0.0
    return (m11, m12, m21, m22, dx, dy), pos


def _parse_transform_table(block: bytes, doubles: bool) -> list[tuple]:
    out: list[tuple] = []
    pos = 0
    while pos < len(block):
        tag, pos = read_mbuint(block, pos)
        xform, pos = _parse_transform_block(block, pos, tag, doubles)
        out.append(xform)
    return out


# --------------------------------------------------------------- stroke layer

def _parse_stroke_block(block: bytes, desc: _StrokeDesc,
                        ) -> tuple[int, dict[int, list[int]], int]:
    """-> (point count, {property tag: raw values}, skipped tail bytes).
    Stroke extended/point properties after the packet+button data are
    skipped (the block size bounds them exactly)."""
    pos = 0
    count, pos = read_mbuint(block, pos)
    arrays: dict[int, list[int]] = {}
    if count:
        for tag in desc.packet_tags:
            if pos >= len(block):
                break
            values, pos = decode_packet_array(block, pos, count)
            arrays[tag] = values
        if desc.button_count and pos < len(block):
            pos += (count * desc.button_count + 7) >> 3  # bit-packed states
    skipped = len(block) - pos
    return count, arrays, skipped


def _normalize(values: list[int], vmin: int, vmax: int) -> list[float]:
    span = vmax - vmin
    if span <= 0:
        return [0.0] * len(values)
    return [min(1.0, max(0.0, (v - vmin) / span)) for v in values]


def _stroke_to_ir(arrays: dict[int, list[int]], da: _DrawAttrs,
                  matrix: tuple, metrics: dict[int, tuple[int, int, float]],
                  ) -> ir.Stroke | None:
    raw_x = arrays.get(TAG_X)
    raw_y = arrays.get(TAG_Y)
    if not raw_x or not raw_y or len(raw_x) != len(raw_y):
        return None
    m11, m12, m21, m22, dx, dy = matrix
    xs = [m11 * x + m21 * y + dx for x, y in zip(raw_x, raw_y)]
    ys = [m12 * x + m22 * y + dy for x, y in zip(raw_x, raw_y)]
    n = len(xs)

    def metric_for(tag: int) -> tuple[int, int, float]:
        return metrics.get(tag) or DEFAULT_METRICS.get(
            tag, (-(2 ** 31), 2 ** 31 - 1, 1.0))

    channels: dict[ir.Channel, list[float]] = {}
    pressure = arrays.get(TAG_NORMAL_PRESSURE)
    if pressure and len(pressure) == n:
        vmin, vmax, _res = metric_for(TAG_NORMAL_PRESSURE)
        channels[ir.Channel.PRESSURE] = _normalize(pressure, vmin, vmax)
    azimuth = arrays.get(TAG_AZIMUTH)
    if azimuth and len(azimuth) == n:
        _mn, _mx, res = metric_for(TAG_AZIMUTH)
        # ISF azimuth: clockwise from north, in degrees/res units;
        # IR: radians CCW from +x [inferred mapping]
        channels[ir.Channel.TILT_AZIMUTH] = [
            math.radians(90.0 - v / res) for v in azimuth]
    altitude = arrays.get(TAG_ALTITUDE)
    if altitude and len(altitude) == n:
        _mn, _mx, res = metric_for(TAG_ALTITUDE)
        channels[ir.Channel.TILT_ALTITUDE] = [
            math.radians(v / res) for v in altitude]
    timer = arrays.get(TAG_TIMER_TICK)
    if timer and len(timer) == n:
        t0 = timer[0]
        # timer ticks assumed milliseconds [inferred]
        channels[ir.Channel.TIMESTAMP] = [(t - t0) / 1000.0 for t in timer]

    width = da.width if da.width is not None else da.height
    if width is None:
        width = _DEFAULT_PEN_WIDTH_HIMETRIC
    elif width == 0.0:
        width = _V1_PEN_WIDTH_WHEN_MISSING
    color = ir.Color(da.color[0] / 255.0, da.color[1] / 255.0,
                     da.color[2] / 255.0)
    opacity = (255 - da.transparency) / 255.0
    is_highlight = da.raster_op == _RASTER_OP_MASK_PEN
    cap = ir.LineCap.SQUARE if da.pen_tip == 1 else ir.LineCap.ROUND

    return ir.Stroke(
        x=xs, y=ys,
        tool=ir.ToolRef(
            family=(ir.ToolFamily.HIGHLIGHTER if is_highlight
                    else ir.ToolFamily.PEN),
            native=ir.NativeTool(FORMAT_ID, "highlighter" if is_highlight
                                 else "pen", {
                "pen_tip": da.pen_tip,
                "pen_style": da.pen_style,
                "raster_op": da.raster_op,
                "drawing_flags": da.flags,
                "width_himetric": da.width,
                "height_himetric": da.height,
                "transparency": da.transparency,
            }),
        ),
        color=color,
        channels=channels,
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT,
            width=float(width),
            color=color,
            opacity=opacity,
            cap=cap,
            underlay=is_highlight,
            blend=ir.BlendMode.DARKEN if is_highlight else ir.BlendMode.NORMAL,
        ),
    )


# ----------------------------------------------------------------- stream walk

_SIZED_TAGS = frozenset({
    TAG_GUID_TABLE, TAG_DRAW_ATTRS_TABLE, TAG_DRAW_ATTRS_BLOCK,
    TAG_STROKE_DESC_TABLE, TAG_STROKE_DESC_BLOCK, TAG_METRIC_TABLE,
    TAG_METRIC_BLOCK, TAG_TRANSFORM_TABLE, TAG_EXTENDED_TRANSFORM_TABLE,
    TAG_STROKE, TAG_COMPRESSION_HEADER, TAG_PERSISTENT_FORMAT,
    TAG_HIMETRIC_SIZE, TAG_STROKE_IDS,
})

_INDEX_TAGS = frozenset({TAG_DIDX, TAG_SIDX, TAG_TIDX, TAG_MIDX})

_BARE_TRANSFORM_TAGS = frozenset({
    TAG_TRANSFORM, TAG_TRANSFORM_ISOTROPIC_SCALE,
    TAG_TRANSFORM_ANISOTROPIC_SCALE, TAG_TRANSFORM_ROTATE,
    TAG_TRANSFORM_TRANSLATE, TAG_TRANSFORM_SCALE_AND_TRANSLATE,
})


def _parse_sized_table(block: bytes, parse_one) -> list:
    """Table = repeated (mbuint size + block) entries."""
    out = []
    pos = 0
    while pos < len(block):
        size, pos = read_mbuint(block, pos)
        if pos + size > len(block):
            raise IsfError("table entry past end of table")
        out.append(parse_one(block[pos:pos + size]))
        pos += size
    return out


def read_isf(data: bytes, title: str = "") -> ir.Document:
    pos = 0
    version, pos = read_mbuint(data, pos)
    if version != 0:
        raise IsfError(f"unsupported ISF version {version}")
    stream_size, pos = read_mbuint(data, pos)
    end = pos + stream_size
    if end > len(data):
        raise IsfError("ISF stream size exceeds available data")

    guids: list[str] = []
    draw_attrs: list[_DrawAttrs] = []
    descriptors: list[_StrokeDesc] = []
    transforms: list[tuple] = []
    metric_blocks: list[dict[int, tuple[int, int, float]]] = []
    ink_rect: tuple[int, int, int, int] | None = None
    himetric_size: tuple[int, int] | None = None
    global_props: dict[str, str] = {}
    strokes: list[ir.Stroke] = []
    da_index = sidx = tidx = midx = 0
    skipped_stroke_bytes = 0
    point_count_total = 0

    while pos < end:
        tag, pos = read_mbuint(data, pos)

        if tag == TAG_INK_SPACE_RECT:
            left, pos = read_mbsint(data, pos)
            top, pos = read_mbsint(data, pos)
            right, pos = read_mbsint(data, pos)
            bottom, pos = read_mbsint(data, pos)
            ink_rect = (left, top, right, bottom)

        elif tag in _INDEX_TAGS:
            value, pos = read_mbuint(data, pos)
            if tag == TAG_DIDX:
                da_index = value
            elif tag == TAG_SIDX:
                sidx = value
            elif tag == TAG_TIDX:
                tidx = value
            else:
                midx = value

        elif tag in _BARE_TRANSFORM_TAGS:
            xform, pos = _parse_transform_block(data, pos, tag)
            transforms = [xform]

        elif tag in _SIZED_TAGS:
            size, pos = read_mbuint(data, pos)
            if pos + size > end:
                raise IsfError("block extends past end of stream")
            block = data[pos:pos + size]
            pos += size
            if tag == TAG_GUID_TABLE:
                guids = [block[i:i + 16].hex()
                         for i in range(0, len(block) - 15, 16)]
            elif tag == TAG_DRAW_ATTRS_TABLE:
                draw_attrs = _parse_sized_table(block, _parse_draw_attrs_block)
            elif tag == TAG_DRAW_ATTRS_BLOCK:
                draw_attrs = [_parse_draw_attrs_block(block)]
            elif tag == TAG_STROKE_DESC_TABLE:
                descriptors = _parse_sized_table(block,
                                                 _parse_stroke_descriptor)
            elif tag == TAG_STROKE_DESC_BLOCK:
                descriptors = [_parse_stroke_descriptor(block)]
            elif tag == TAG_METRIC_TABLE:
                metric_blocks = _parse_sized_table(block, _parse_metric_block)
            elif tag == TAG_METRIC_BLOCK:
                metric_blocks = [_parse_metric_block(block)]
            elif tag == TAG_TRANSFORM_TABLE:
                transforms = _parse_transform_table(block, doubles=False)
            elif tag == TAG_EXTENDED_TRANSFORM_TABLE:
                # double-precision re-statement of the transform table
                transforms = _parse_transform_table(block, doubles=True)
            elif tag == TAG_HIMETRIC_SIZE:
                p = 0
                w, p = read_mbsint(block, p)
                h, p = read_mbsint(block, p)
                himetric_size = (w, h)
            elif tag == TAG_STROKE:
                desc = (descriptors[sidx] if sidx < len(descriptors)
                        else _DEFAULT_DESC)
                da = (draw_attrs[da_index] if da_index < len(draw_attrs)
                      else _DrawAttrs())
                matrix = transforms[tidx] if tidx < len(transforms) \
                    else _IDENTITY
                metrics = metric_blocks[midx] if midx < len(metric_blocks) \
                    else {}
                count, arrays, skipped = _parse_stroke_block(block, desc)
                skipped_stroke_bytes += skipped
                point_count_total += count
                stroke = _stroke_to_ir(arrays, da, matrix, metrics)
                if stroke is not None:
                    strokes.append(stroke)
            # CompressionHeader / PersistentFormat / StrokeIds: skipped

        elif tag >= KNOWN_TAG_BASE:
            # global property (predefined GUID or custom via GUID table)
            payload, pos = _skip_property_payload(data, pos, tag)
            if tag >= CUSTOM_TAG_BASE:
                idx = tag - CUSTOM_TAG_BASE
                key = guids[idx] if idx < len(guids) else f"custom-{tag}"
            else:
                key = GUID_PROPERTY_NAMES.get(tag - KNOWN_TAG_BASE, str(tag))
            global_props[key] = payload.hex()

        else:
            # unknown structural tag: size-prefixed, skip [verified: WPF]
            size, pos = read_mbuint(data, pos)
            if pos + size > end:
                raise IsfError("unknown block extends past end of stream")
            _logger.debug("isf: skipping unknown tag %d (%d bytes)", tag, size)
            pos += size

    # page bounds: content bbox, unioned with the ink-space rect when given
    xs = [x for s in strokes for x in s.x]
    ys = [y for s in strokes for y in s.y]
    if xs:
        bounds = ir.Rect(min(xs), min(ys), max(xs), max(ys))
    else:
        bounds = ir.Rect(0.0, 0.0, 21000.0, 27900.0)  # A4-ish himetric
    if ink_rect is not None:
        bounds = ir.Rect(min(bounds.x_min, ink_rect[0]),
                         min(bounds.y_min, ink_rect[1]),
                         max(bounds.x_max, ink_rect[2]),
                         max(bounds.y_max, ink_rect[3]))

    metadata: dict = {"stroke_count": len(strokes),
                      "point_count": point_count_total}
    if ink_rect is not None:
        metadata["ink_space_rect"] = list(ink_rect)
    if himetric_size is not None:
        metadata["himetric_size"] = list(himetric_size)
    if guids:
        metadata["custom_guids"] = guids
    if global_props:
        metadata["properties"] = global_props
    if skipped_stroke_bytes:
        metadata["skipped_stroke_property_bytes"] = skipped_stroke_bytes

    return ir.Document(
        format_id=FORMAT_ID,
        title=title,
        pages=[ir.Page(bounds=bounds, point_scale=POINT_SCALE,
                       layers=[ir.Layer(strokes=strokes)])],
        metadata=metadata,
    )


class IsfReader:
    format_id = FORMAT_ID
    extensions = (".isf",)

    def detect(self, path: Path) -> bool:
        try:
            with open(path, "rb") as f:
                head = f.read(32)
            if not head or head[0] != 0x00:  # version must be 0
                return False
            size_bytes = path.stat().st_size
            pos = 1
            size, pos = read_mbuint(head, pos)
            if size_bytes - pos == size:
                return True
            # Some containers concatenate several ISF streams back to
            # back (observed in Microsoft's own test data); accept when
            # the declared size fits and the first tag is plausible.
            if size > 0 and size_bytes - pos > size and pos < len(head):
                tag, _ = read_mbuint(head, pos)
                return tag <= TAG_EXTENDED_TRANSFORM_TABLE or \
                    tag >= KNOWN_TAG_BASE
            return False
        except (OSError, IsfError, IndexError):
            return False

    def read(self, path: Path) -> ir.Document:
        return read_isf(path.read_bytes(), title=path.stem)
