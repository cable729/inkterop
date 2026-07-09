"""Generate the synthetic ISF fixtures in this directory.

This is a from-scratch ISF *encoder* written against the MS-ISF spec
(Microsoft Open Specification Promise) and the MIT-licensed WPF codec
(github.com/dotnet/wpf). Writing the encoder doubles as spec
verification: the low-level primitives it shares with the reader
(multibyte ints, Huffman tables, bit packing, delta-delta) are pinned
byte-for-byte by hand-computed values in tests/test_isf.py, so the
encoder/decoder pair is not a closed self-confirming loop.

Run:
    cd core
    uv run python tests/fixtures/isf/make_fixture.py

Fixtures (all CC0-1.0, no third-party ink data):
- pen-pressure-tilt.isf: ink-space rect, drawing-attributes block (blue,
  150 himetric width), stroke descriptor (pressure, azimuth, altitude,
  timer ticks), metric block (pressure 0..4096), two strokes. Exercises
  indexed-Huffman (X/Y/pressure/timer), raw bit-packing (azimuth) and
  delta-delta bit-packing (altitude).
- highlighter.isf: yellow half-transparent MaskPen (raster op 9) stroke,
  rectangle pen tip. Exercises the uncompressed path (algo 0x00 =
  big-endian int32) and plain bit-packing.
- xy-only.isf: no tables at all -- the spec's "simple example" (default
  descriptor, default drawing attributes), one Huffman stroke.
"""
from __future__ import annotations

import math
import struct
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parents[2] / "src"))

from inkterop.formats.isf import (  # noqa: E402
    TAG_ALTITUDE, TAG_AZIMUTH, TAG_COLORREF, TAG_DRAW_ATTRS_BLOCK,
    TAG_INK_SPACE_RECT, TAG_METRIC_BLOCK, TAG_NORMAL_PRESSURE,
    TAG_PEN_TIP, TAG_PEN_WIDTH, TAG_ROP, TAG_STROKE, TAG_STROKE_DESC_BLOCK,
    TAG_TIMER_TICK, TAG_TRANSPARENCY, BitWriter, DeltaDelta, HuffmanCodec,
    write_mbsint, write_mbuint,
)

# ------------------------------------------------------------ packet encoders


def huffman_packets(values: list[int], index: int = 2) -> bytes:
    """Indexed-Huffman packet array (delta-delta + Huffman table)."""
    codec = HuffmanCodec(index)
    dd = DeltaDelta()
    writer = BitWriter()
    for v in values:
        xf, extra = dd.transform(v)
        codec.encode_one(xf, extra, writer)
    return bytes([0x80 | index]) + writer.getvalue()


def bitpack_packets(values: list[int], deldel: bool = False) -> bytes:
    """Gorilla bit-packed packet array (optionally delta-delta)."""
    head = b""
    rest = values
    if deldel:
        dd = DeltaDelta()
        transformed = []
        for v in values:
            xf, extra = dd.transform(v)
            assert extra == 0, "fixture values must fit in 32 bits"
            transformed.append(xf)
        head = write_mbsint(transformed[0]) + write_mbsint(transformed[1])
        rest = transformed[2:]
    bits = max((abs(v).bit_length() for v in rest), default=0) + 1  # sign bit
    assert bits <= 31
    writer = BitWriter()
    for v in rest:
        writer.write(v & ((1 << bits) - 1), bits)  # two's complement
    algo = (0x20 if deldel else 0x00) | bits
    return bytes([algo]) + head + writer.getvalue()


def raw_packets(values: list[int]) -> bytes:
    """Uncompressed packet array: algo 0x00 = 32-bit big-endian ints."""
    return b"\x00" + b"".join(struct.pack(">i", v) for v in values)


# ----------------------------------------------------------- container pieces


def tagged(tag: int, payload: bytes) -> bytes:
    return write_mbuint(tag) + write_mbuint(len(payload)) + payload


def ink_space_rect(left: int, top: int, right: int, bottom: int) -> bytes:
    return (write_mbuint(TAG_INK_SPACE_RECT) + write_mbsint(left)
            + write_mbsint(top) + write_mbsint(right) + write_mbsint(bottom))


def metric_entry(tag: int, vmin: int, vmax: int, unit: int,
                 resolution: float) -> bytes:
    data = (write_mbsint(vmin) + write_mbsint(vmax) + write_mbuint(unit)
            + struct.pack("<f", resolution))
    return write_mbuint(tag) + write_mbuint(len(data)) + data


def stroke(arrays: list[bytes], count: int) -> bytes:
    return tagged(TAG_STROKE, write_mbuint(count) + b"".join(arrays))


def document(body: bytes) -> bytes:
    return write_mbuint(0) + write_mbuint(len(body)) + body


# ----------------------------------------------------------------- fixtures
# All coordinates are HIMETRIC (0.01 mm units).

def make_pen_fixture() -> bytes:
    n = 8
    xs = [1000 + 400 * i for i in range(n)]
    ys = [2000 + int(300 * math.sin(i * 0.8)) for i in range(n)]
    pressure = [400 + 400 * i for i in range(n)]     # 400..3200 of 0..4096
    azimuth = [900] * n                              # 90.0 deg, raw bitpack
    altitude = [450] * n                             # 45.0 deg, deldel pack
    timer = [5000 + 10 * i for i in range(n)]        # ms ticks

    xs2 = [1000 + 250 * i for i in range(5)]
    ys2 = [4000] * 5
    pressure2 = [2048] * 5
    azimuth2 = [0] * 5
    altitude2 = [900] * 5
    timer2 = [6000 + 10 * i for i in range(5)]

    da = tagged(TAG_DRAW_ATTRS_BLOCK,
                write_mbuint(TAG_PEN_WIDTH) + write_mbuint(150)
                + write_mbuint(TAG_COLORREF) + write_mbuint(0x00FF0000))  # blue

    desc = tagged(TAG_STROKE_DESC_BLOCK,
                  write_mbuint(TAG_NORMAL_PRESSURE)
                  + write_mbuint(TAG_AZIMUTH)
                  + write_mbuint(TAG_ALTITUDE)
                  + write_mbuint(TAG_TIMER_TICK))

    metrics = tagged(TAG_METRIC_BLOCK,
                     metric_entry(TAG_NORMAL_PRESSURE, 0, 4096, 0, 1.0))

    body = (
        ink_space_rect(0, 0, 20000, 15000)
        + da + desc + metrics
        + stroke([huffman_packets(xs), huffman_packets(ys),
                  huffman_packets(pressure), bitpack_packets(azimuth),
                  bitpack_packets(altitude, deldel=True),
                  huffman_packets(timer)], n)
        + stroke([huffman_packets(xs2), huffman_packets(ys2),
                  huffman_packets(pressure2), bitpack_packets(azimuth2),
                  bitpack_packets(altitude2, deldel=True),
                  huffman_packets(timer2)], 5)
    )
    return document(body)


def make_highlighter_fixture() -> bytes:
    n = 6
    xs = [500 + 700 * i for i in range(n)]
    ys = [1500 + 40 * i for i in range(n)]

    da = tagged(TAG_DRAW_ATTRS_BLOCK,
                write_mbuint(TAG_PEN_WIDTH) + write_mbuint(600)
                + write_mbuint(TAG_COLORREF) + write_mbuint(0x0000FFFF)  # yellow
                + write_mbuint(TAG_TRANSPARENCY) + write_mbuint(100)
                + write_mbuint(TAG_PEN_TIP) + write_mbuint(1)  # rectangle
                + write_mbuint(TAG_ROP) + bytes([9, 0, 0, 0]))  # MaskPen

    body = da + stroke([raw_packets(xs), bitpack_packets(ys)], n)
    return document(body)


def make_xy_fixture() -> bytes:
    xs = [100, 200, 350, 500, 600]
    ys = [100, 180, 150, 220, 300]
    body = stroke([huffman_packets(xs), huffman_packets(ys)], 5)
    return document(body)


if __name__ == "__main__":
    for name, data in (("pen-pressure-tilt.isf", make_pen_fixture()),
                       ("highlighter.isf", make_highlighter_fixture()),
                       ("xy-only.isf", make_xy_fixture())):
        out = HERE / name
        out.write_bytes(data)
        print(f"wrote {out} ({len(data)} bytes)")
