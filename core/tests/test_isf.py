"""Microsoft ISF (Ink Serialized Format) reader tests.

Fixtures are self-generated (tests/fixtures/isf/make_fixture.py) with an
independent encoder; the primitive layer is additionally pinned against
hand-computed byte sequences derived from the MS-ISF spec so the
encoder/decoder pair is not a closed loop.

Oracle status: no permissively-licensed pip-installable ISF stroke
decoder exists (isf-qt is GPL C++; Microsoft's reference codec lives in
dotnet/wpf, MIT but .NET-only), so there is no importorskip oracle test.
"""
from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest

from inkterop import ir
from inkterop.formats.isf import (
    HUFFMAN_BAA_DATA, BitReader, BitWriter, DeltaDelta, HuffmanCodec,
    IsfError, IsfReader, decode_packet_array, decompress_property, read_isf,
    read_mbsint, read_mbuint, write_mbsint, write_mbuint,
)

FIXTURES = Path(__file__).parent / "fixtures"
ISF = FIXTURES / "isf"


def _find_corpus() -> Path:
    """corpus/ lives at the repo root, gitignored; in linked worktrees it
    only exists in the main checkout, so walk up the ancestors."""
    for base in Path(__file__).resolve().parents[2:]:
        candidate = base / "corpus" / "third-party" / "wpf-test-isf"
        if candidate.is_dir():
            return candidate
    return Path(__file__).parents[2] / "corpus" / "third-party" / \
        "wpf-test-isf"


CORPUS = _find_corpus()


# --------------------------------------------------------------- multibyte

def test_mbuint_hand_computed():
    # spec: 7-bit little-endian groups, continuation high bit
    assert read_mbuint(b"\x00", 0) == (0, 1)
    assert read_mbuint(b"\x7f", 0) == (127, 1)
    assert read_mbuint(b"\x80\x01", 0) == (128, 2)
    assert read_mbuint(b"\xff\x7f", 0) == (16383, 2)
    assert read_mbuint(b"\x80\x80\x01", 0) == (16384, 3)
    for v in (0, 1, 127, 128, 300, 16383, 16384, 2 ** 31 - 1):
        buf = write_mbuint(v)
        assert read_mbuint(buf, 0) == (v, len(buf))


def test_mbsint_sign_flip():
    # signed = (abs << 1) | sign -- sign-flip, NOT zigzag
    assert write_mbsint(0) == b"\x00"
    assert write_mbsint(1) == b"\x02"
    assert write_mbsint(-1) == b"\x03"
    assert write_mbsint(63) == b"\x7e"
    assert write_mbsint(64) == b"\x80\x01"  # 2 bytes once |v| >= 64
    assert read_mbsint(b"\x03", 0) == (-1, 1)
    assert read_mbsint(b"\x02", 0) == (1, 1)
    for v in (0, 1, -1, 63, -64, 8191, -8192, 2 ** 20):
        buf = write_mbsint(v)
        assert read_mbsint(buf, 0) == (v, len(buf))


def test_mbuint_truncated():
    with pytest.raises(IsfError):
        read_mbuint(b"\x80", 0)


# ------------------------------------------------------------------ bit I/O

def test_bit_io_msb_first():
    w = BitWriter()
    w.write(0b101, 3)
    w.write(0b1, 1)
    w.write(0xAB, 8)
    data = w.getvalue()
    assert data == bytes([0b10111010, 0b10110000])
    r = BitReader(data)
    assert r.read(3) == 0b101
    assert r.read(1) == 1
    assert r.read(8) == 0xAB
    assert r.bytes_consumed == 2


# -------------------------------------------------------------- delta-delta

def test_delta_delta_known_sequence():
    dd = DeltaDelta()
    out = [dd.transform(v)[0] for v in (10, 12, 15, 15)]
    # dd_i = v_i + v_{i-2} - 2*v_{i-1} with zero-initialized state
    assert out == [10, -8, 1, -3]
    inv = DeltaDelta()
    assert [inv.inverse(v) for v in out] == [10, 12, 15, 15]


def test_delta_delta_64bit_escape():
    dd = DeltaDelta()
    xf, extra = dd.transform(0x7FFFFFFF)
    assert (xf, extra) == (0x7FFFFFFF, 0)
    xf, extra = dd.transform(-0x7FFFFFFF)  # |dd| = 3*0x7FFFFFFF > int32
    assert extra != 0
    inv = DeltaDelta()
    assert inv.inverse(0x7FFFFFFF, 0) == 0x7FFFFFFF
    assert inv.inverse(xf, extra) == -0x7FFFFFFF


# ------------------------------------------------------------------ Huffman

def test_huffman_tables_match_spec():
    # spec appendix DEF_BAA_SIZE = {10, 10, 10, 10, 9, 8, 7, 7}
    assert [len(t) for t in HUFFMAN_BAA_DATA] == [10, 10, 10, 10, 9, 8, 7, 7]
    # mins derived by hand per the spec's InitHuffTable for table 2
    # (bits 0,1,1,1,2,4,8,14,22,32): lower bound grows by 1<<(bits[n]-1)
    codec = HuffmanCodec(2)
    assert codec.mins == [0, 1, 2, 3, 4, 6, 14, 142, 8334, 2105486]


def test_huffman_hand_computed_bits():
    """Encode [0, 1, -1, 2, 4] with table 2; exact bytes derived by hand
    from the spec's Encode() algorithm (data lengths 1,1,1,2 bits for
    prefix lengths 2,2,3,5; mins 1,1,2,4):
      0 -> '0'; 1 -> '10'+'0'; -1 -> '10'+'1'; 2 -> '110'+'0';
      4 -> '11110'+'00'  ==>  01001011 10011110 00......
    """
    codec = HuffmanCodec(2)
    w = BitWriter()
    for v in (0, 1, -1, 2, 4):
        codec.encode_one(v, 0, w)
    assert w.getvalue() == bytes([0x4B, 0x9E, 0x00])

    r = BitReader(bytes([0x4B, 0x9E, 0x00]))
    assert [codec.decode_one(r)[0] for _ in range(5)] == [0, 1, -1, 2, 4]


@pytest.mark.parametrize("index", range(8))
def test_huffman_roundtrip_all_tables(index):
    codec = HuffmanCodec(index)
    values = [0, 1, -1, 2, -3, 7, -13, 140, -141, 8332, 100000, -2 ** 20,
              2 ** 30, -(2 ** 30)]
    w = BitWriter()
    for v in values:
        codec.encode_one(v, 0, w)
    r = BitReader(w.getvalue())
    assert [codec.decode_one(r)[0] for _ in values] == values


def test_huffman_bad_table_index():
    with pytest.raises(IsfError):
        HuffmanCodec(8)  # custom-codec range: unsupported
    with pytest.raises(IsfError):
        decode_packet_array(b"\x88\x00", 0, 1)


# ------------------------------------------------------------- packet arrays

def test_packet_array_bitpacked_hand_computed():
    # algo 0x03: 3 bits/value, two's complement: 1->001, -2->110, 3->011
    # bit stream '001110011' -> bytes 0x39 0x80
    data = bytes([0x03, 0x39, 0x80])
    values, pos = decode_packet_array(data, 0, 3)
    assert values == [1, -2, 3]
    assert pos == len(data)


def test_packet_array_raw_int32():
    # algo 0x00 == bit-pack with 32 bits == big-endian int32s
    data = b"\x00" + struct.pack(">3i", 70000, -5, 0)
    values, pos = decode_packet_array(data, 0, 3)
    assert values == [70000, -5, 0]
    assert pos == len(data)


def test_packet_array_deldel_bitpacked():
    """algo 0x20|bits: first two delta-deltas are multibyte sign-encoded,
    the remaining n-2 bit-packed. Values [100, 110, 120]:
    dd = [100, -90, 0]; head = mbsint(100) mbsint(-90); then 0 in 2 bits."""
    head = write_mbsint(100) + write_mbsint(-90)
    data = bytes([0x22]) + head + bytes([0x00])  # 2 bits: '00' + pad
    values, pos = decode_packet_array(data, 0, 3)
    assert values == [100, 110, 120]
    assert pos == len(data)


def test_packet_array_huffman_with_deldel():
    """The indexed-Huffman path always applies delta-delta: constant
    input [7, 7, 7] transforms to [7, -7, 0]."""
    codec = HuffmanCodec(2)
    dd = DeltaDelta()
    w = BitWriter()
    for v in (7, 7, 7):
        xf, extra = dd.transform(v)
        codec.encode_one(xf, extra, w)
    data = bytes([0x82]) + w.getvalue()
    values, pos = decode_packet_array(data, 0, 3)
    assert values == [7, 7, 7]
    assert pos == len(data)


def test_packet_array_rejects_property_algo():
    with pytest.raises(IsfError):
        decode_packet_array(b"\xc0\x00", 0, 1)  # DEFAULT_COMPRESSION byte


# ----------------------------------------------------------- property arrays

def test_property_identity_bytes():
    # algo 0x00: byte-oriented, index 0 -> (8 bits, 0 pad) == raw copy
    assert decompress_property(b"\x00\x01\xfe\x7f") == b"\x01\xfe\x7f"


def test_property_bitpacked_bytes():
    # byte type, index 13 -> 3 bits/value, 0 pads: values 1,2,3,4,5,6,7,0
    w = BitWriter()
    for v in (1, 2, 3, 4, 5, 6, 7, 0):
        w.write(v, 3)
    out = decompress_property(bytes([13]) + w.getvalue())
    assert out == bytes([1, 2, 3, 4, 5, 6, 7, 0])


def test_property_lz_unsupported():
    with pytest.raises(IsfError):
        decompress_property(b"\x80\x00")


# ------------------------------------------------------------------- detect

def test_detect():
    reader = IsfReader()
    for name in ("xy-only.isf", "highlighter.isf", "pen-pressure-tilt.isf"):
        assert reader.detect(ISF / name), name
    # other formats' magic bytes must be rejected
    assert not reader.detect(FIXTURES / "saber" / "saber-mac-pens-text.sba")
    assert not reader.detect(FIXTURES / "uim" / "two-strokes-pressure.uim")
    assert not reader.detect(FIXTURES / "remarkable" / "ballpoint-small.rm")
    assert not reader.detect(ISF / "does-not-exist.isf")


def test_detect_rejects_wrong_size(tmp_path):
    bogus = tmp_path / "bogus.isf"
    bogus.write_bytes(b"\x00\x30" + b"\x00" * 5)  # size says 0x30, has 5
    assert not IsfReader().detect(bogus)


# ------------------------------------------------------------------ fixtures

def test_read_xy_only():
    doc = IsfReader().read(ISF / "xy-only.isf")
    doc.validate()
    assert doc.format_id == "isf"
    assert len(doc.pages) == 1
    page = doc.pages[0]
    assert page.point_scale == pytest.approx(72.0 / 2540.0)
    strokes = list(page.strokes())
    assert len(strokes) == 1
    s = strokes[0]
    assert s.x == [100.0, 200.0, 350.0, 500.0, 600.0]
    assert s.y == [100.0, 180.0, 150.0, 220.0, 300.0]
    assert not s.channels
    # spec defaults: black pen, v2 default width 53 himetric
    assert s.color.rgb() == (0.0, 0.0, 0.0)
    assert s.appearance.width == pytest.approx(53.0)
    assert s.appearance.mode is ir.GeometryMode.STROKED_CONSTANT


def test_read_pen_pressure_tilt():
    doc = IsfReader().read(ISF / "pen-pressure-tilt.isf")
    doc.validate()
    strokes = list(doc.pages[0].strokes())
    assert len(strokes) == 2
    wave, line = strokes

    # geometry as authored by make_fixture.py
    assert len(wave) == 8
    assert wave.x == [1000.0 + 400 * i for i in range(8)]
    assert wave.y[0] == 2000.0
    assert len(line) == 5
    assert line.y == [4000.0] * 5

    # pressure normalized against the metric block (0..4096)
    p = wave.channels[ir.Channel.PRESSURE]
    assert p == pytest.approx([(400 + 400 * i) / 4096 for i in range(8)])
    assert all(0.0 <= v <= 1.0 for v in p)
    assert line.channels[ir.Channel.PRESSURE] == pytest.approx([0.5] * 5)

    # azimuth 90deg -> 0 rad; azimuth 0deg -> pi/2 [inferred mapping]
    assert wave.channels[ir.Channel.TILT_AZIMUTH] == pytest.approx([0.0] * 8)
    assert line.channels[ir.Channel.TILT_AZIMUTH] == pytest.approx(
        [math.pi / 2] * 5)
    # altitude uses the default 0.1-degree resolution
    assert wave.channels[ir.Channel.TILT_ALTITUDE] == pytest.approx(
        [math.pi / 4] * 8)
    assert line.channels[ir.Channel.TILT_ALTITUDE] == pytest.approx(
        [math.pi / 2] * 5)

    # timer ticks -> seconds since stroke start
    for s in (wave, line):
        ts = s.channels[ir.Channel.TIMESTAMP]
        assert ts[0] == 0.0
        assert ts == pytest.approx([0.01 * i for i in range(len(s))])

    # drawing attributes: blue, 150 himetric wide, opaque pen
    for s in (wave, line):
        assert s.color.rgb() == (0.0, 0.0, 1.0)
        assert s.appearance.width == pytest.approx(150.0)
        assert s.appearance.opacity == pytest.approx(1.0)
        assert not s.appearance.underlay
        assert s.tool.family is ir.ToolFamily.PEN
        assert s.tool.native.format_id == "isf"

    # ink-space rect recorded and page bounds contain every point
    assert doc.metadata["ink_space_rect"] == [0, 0, 20000, 15000]
    b = doc.pages[0].bounds
    for s in strokes:
        assert all(b.x_min <= x <= b.x_max for x in s.x)
        assert all(b.y_min <= y <= b.y_max for y in s.y)


def test_read_highlighter():
    doc = IsfReader().read(ISF / "highlighter.isf")
    doc.validate()
    strokes = list(doc.pages[0].strokes())
    assert len(strokes) == 1
    s = strokes[0]
    assert len(s) == 6
    assert s.x == [500.0 + 700 * i for i in range(6)]

    # MaskPen raster op => highlighter: underlay + darken
    assert s.tool.family is ir.ToolFamily.HIGHLIGHTER
    assert s.appearance.underlay
    assert s.appearance.blend is ir.BlendMode.DARKEN
    assert s.tool.native.params["raster_op"] == 9

    # COLORREF yellow, transparency byte 100 -> opacity (255-100)/255
    assert s.color.rgb() == (1.0, 1.0, 0.0)
    assert s.appearance.opacity == pytest.approx(155 / 255)
    assert s.appearance.width == pytest.approx(600.0)
    assert s.appearance.cap is ir.LineCap.SQUARE  # rectangle pen tip

    b = doc.pages[0].bounds
    assert all(b.x_min <= x <= b.x_max for x in s.x)
    assert all(b.y_min <= y <= b.y_max for y in s.y)


def test_read_empty_stream():
    doc = read_isf(b"\x00\x00")  # version 0, size 0: valid empty ink
    doc.validate()
    assert list(doc.pages[0].strokes()) == []


def test_read_rejects_bad_version():
    with pytest.raises(IsfError):
        read_isf(b"\x01\x00")


def test_read_rejects_truncated():
    with pytest.raises(IsfError):
        read_isf(b"\x00\x7f\x0a")  # claims 127 bytes, has 1


# ------------------------------------------------------------------- corpus
# Real ISF written by Microsoft's own encoder (dotnet/wpf-test, MIT);
# exercises Huffman tables 2-7 and the plain bit-packing fallback.

def _corpus_files() -> list[Path]:
    if not CORPUS.is_dir():
        return []
    # gif/base64gif are fortified-GIF variants: out of scope by design
    return sorted(p for p in CORPUS.glob("*.isf")
                  if "gif" not in p.name.lower())


@pytest.mark.skipif(not _corpus_files(), reason="corpus samples not present")
@pytest.mark.parametrize("path", _corpus_files(), ids=lambda p: p.stem)
def test_corpus_real_microsoft_isf(path: Path):
    reader = IsfReader()
    assert reader.detect(path)
    doc = reader.read(path)
    doc.validate()
    strokes = list(doc.pages[0].strokes())
    assert len(strokes) > 0
    assert all(len(s) > 0 for s in strokes)
    b = doc.pages[0].bounds
    for s in strokes:
        assert all(b.x_min <= x <= b.x_max for x in s.x)
        assert all(b.y_min <= y <= b.y_max for y in s.y)
        for ch, values in s.channels.items():
            rng = ir.CHANNEL_RANGE.get(ch)
            if rng:
                assert all(rng[0] <= v <= rng[1] for v in values), ch


@pytest.mark.skipif(not (CORPUS / "InkCanvas-lasso.isf").is_file(),
                    reason="corpus samples not present")
def test_corpus_pressure_channel():
    doc = IsfReader().read(CORPUS / "InkCanvas-lasso.isf")
    s = next(iter(doc.pages[0].strokes()))
    p = s.channels[ir.Channel.PRESSURE]
    assert len(p) == len(s)
    assert 0.0 < min(p) < max(p) < 1.0  # a real ramp, properly normalized


# ---------------------------------------------------------------- conversion

def test_convert_to_pdf_smoke(tmp_path):
    from inkterop import formats
    from inkterop.convert import convert

    if not any(r.format_id == "isf" for r in formats.readers()):
        formats.register_reader(IsfReader())
    out = tmp_path / "pen.pdf"
    doc = convert(ISF / "pen-pressure-tilt.isf", out)
    assert out.stat().st_size > 0
    assert doc.format_id == "isf"
    assert len(list(doc.pages[0].strokes())) == 2
