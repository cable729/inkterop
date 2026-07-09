"""Wacom Universal Ink Model (.uim) reader tests.

Fixture is self-generated with Wacom's Apache-2.0 reference encoder
(tests/fixtures/uim/make_fixture.py); corpus samples and the oracle
comparison are gated on their availability.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from inkterop import ir
from inkterop.formats.uim import (
    UimReader, _delta_decode, parse_message, read_varint, zigzag,
)

FIXTURE = Path(__file__).parent / "fixtures" / "uim" / \
    "two-strokes-pressure.uim"
def _find_corpus() -> Path:
    """corpus/ lives at the repo root, gitignored; in linked worktrees it
    only exists in the main checkout, so walk up the ancestors."""
    for base in Path(__file__).resolve().parents[2:]:
        candidate = base / "corpus" / "third-party" / \
            "universal-ink-library" / "ink"
        if candidate.is_dir():
            return candidate
    return Path(__file__).parents[2] / "corpus" / "third-party" / \
        "universal-ink-library" / "ink"


CORPUS = _find_corpus()


# ------------------------------------------------------------- wire walker

def test_parse_message_wire_types():
    msg = b"".join([
        b"\x08\xac\x02",              # field 1, varint 300
        b"\x15\x00\x00\x80\x3f",      # field 2, fixed32 1.0f
        b"\x1a\x03abc",               # field 3, bytes "abc"
        b"\x21" + struct.pack("<d", 2.5),  # field 4, fixed64 2.5
        b"\x2a\x03\x02\x04\x06",      # field 5, packed varints [2,4,6]
    ])
    fields = parse_message(msg)
    assert fields[1] == [(0, 300)]
    assert struct.unpack("<f", fields[2][0][1])[0] == 1.0
    assert fields[3] == [(2, b"abc")]
    assert struct.unpack("<d", fields[4][0][1])[0] == 2.5
    packed = fields[5][0][1]
    pos, out = 0, []
    while pos < len(packed):
        v, pos = read_varint(packed, pos)
        out.append(v)
    assert out == [2, 4, 6]


def test_zigzag():
    assert [zigzag(v) for v in (0, 1, 2, 3, 4)] == [0, -1, 1, -2, 2]


def test_delta_decode():
    # deltas [100, -10, 5] at precision 1 -> cumsum/10
    assert _delta_decode([100, -10, 5], 1) == pytest.approx([10.0, 9.0, 9.5])
    # timestamp-style: resolution 1000, start ms -> seconds
    out = _delta_decode([0, 10, 10], 0, 1000.0, 5000.0)
    assert out == pytest.approx([5.0, 5.01, 5.02])


# ------------------------------------------------------------------ detect

def test_detect():
    reader = UimReader()
    assert reader.detect(FIXTURE)
    here = Path(__file__).parent
    assert not reader.detect(
        here / "fixtures" / "saber" / "saber-mac-pens-text.sba")
    assert not reader.detect(
        here / "fixtures" / "remarkable" / "ballpoint-small.rm")
    assert not reader.detect(here / "does-not-exist.uim")


# ----------------------------------------------------------------- fixture

def test_read_fixture():
    doc = UimReader().read(FIXTURE)
    doc.validate()
    assert doc.metadata["uim_version"] == "3.1.0"
    assert doc.metadata["properties"]["Title"] == "inkterop synthetic fixture"
    assert len(doc.pages) == 1

    strokes = list(doc.pages[0].strokes())
    assert len(strokes) == 2
    wave, line = strokes

    # geometry as authored by make_fixture.py
    assert len(wave) == 10
    assert wave.x[0] == pytest.approx(20.0)
    assert wave.x[-1] == pytest.approx(110.0)
    assert wave.y[0] == pytest.approx(100.0)
    assert len(line) == 5
    assert line.y == pytest.approx([200.0] * 5)

    # channels present, ranges honored
    for s in strokes:
        for ch in (ir.Channel.PRESSURE, ir.Channel.TIMESTAMP,
                   ir.Channel.TILT_AZIMUTH, ir.Channel.TILT_ALTITUDE,
                   ir.Channel.WIDTH):
            assert ch in s.channels, ch
            assert len(s.channels[ch]) == len(s)
        assert all(0.0 <= p <= 1.0 for p in s.channels[ir.Channel.PRESSURE])
        ts = s.channels[ir.Channel.TIMESTAMP]
        assert ts[0] == 0.0 and ts == sorted(ts)  # seconds since start

    assert max(wave.channels[ir.Channel.PRESSURE]) > 0.4  # real ramp
    assert wave.channels[ir.Channel.TILT_ALTITUDE] == pytest.approx([1.0] * 10)

    # style: variable-width blue pen, constant-width translucent red line
    assert wave.appearance.mode is ir.GeometryMode.STROKED_VARIABLE
    assert wave.color.b == pytest.approx(0.8, abs=1 / 255)
    assert wave.appearance.opacity == pytest.approx(1.0)
    assert line.appearance.mode is ir.GeometryMode.STROKED_CONSTANT
    assert line.appearance.width == pytest.approx(8.0)
    assert line.appearance.opacity == pytest.approx(0.5, abs=1 / 255)
    assert line.tool.family is ir.ToolFamily.HIGHLIGHTER  # brush name
    assert wave.tool.family is ir.ToolFamily.PEN
    assert wave.tool.native.params["brush_uri"].endswith("FixturePen")

    # bounds containment
    b = doc.pages[0].bounds
    for s in strokes:
        assert all(b.x_min <= x <= b.x_max for x in s.x)
        assert all(b.y_min <= y <= b.y_max for y in s.y)


# ------------------------------------------------------------------ corpus

def _corpus_files(subdir: str) -> list[Path]:
    d = CORPUS / subdir
    return sorted(d.glob("*.uim")) if d.is_dir() else []


@pytest.mark.skipif(not _corpus_files("uim_3.1.0"),
                    reason="corpus samples not present")
@pytest.mark.parametrize("path", _corpus_files("uim_3.1.0"),
                         ids=lambda p: p.stem)
def test_corpus_310(path: Path):
    reader = UimReader()
    assert reader.detect(path)
    doc = reader.read(path)
    doc.validate()
    strokes = list(doc.pages[0].strokes())
    assert len(strokes) > 0
    assert all(len(s) > 0 for s in strokes)
    assert doc.metadata["uim_version"] == "3.1.0"


@pytest.mark.skipif(not _corpus_files("uim_3.0.0"),
                    reason="corpus samples not present")
@pytest.mark.parametrize("path", _corpus_files("uim_3.0.0"),
                         ids=lambda p: p.stem)
def test_corpus_300(path: Path):
    doc = UimReader().read(path)
    doc.validate()
    assert len(list(doc.pages[0].strokes())) > 0
    assert doc.metadata["uim_version"] == "3.0.0"


@pytest.mark.skipif(not _corpus_files("uim_3.0.0"),
                    reason="corpus samples not present")
def test_corpus_300_310_same_ink():
    """The 3.1 'delta' corpus files re-encode the 3.0 documents; the two
    decode paths must agree on the geometry."""
    p300 = CORPUS / "uim_3.0.0" / "4) Hello World 1.uim"
    p310 = CORPUS / "uim_3.1.0" / "4) Hello World 1 (3.1 delta).uim"
    d300 = UimReader().read(p300)
    d310 = UimReader().read(p310)
    s300 = list(d300.pages[0].strokes())
    s310 = list(d310.pages[0].strokes())
    assert len(s300) == len(s310)
    for a, b in zip(s300, s310):
        assert len(a) == len(b)
        # 3.1 compressed splines quantize at 10^-2
        assert b.x == pytest.approx(a.x, abs=0.02)
        assert b.y == pytest.approx(a.y, abs=0.02)


# ------------------------------------------------------------------ oracle

def test_oracle_against_wacom_library():
    """Compare our x/y decode numerically against Wacom's own parser."""
    uim_parser = pytest.importorskip(
        "uim.codec.parser.uim", reason="universal-ink-library not installed")
    model = uim_parser.UIMParser().parse(str(FIXTURE))
    ours = list(UimReader().read(FIXTURE).pages[0].strokes())
    theirs = model.strokes
    assert len(ours) == len(theirs)
    for s_ir, s_lib in zip(ours, theirs):
        lx, ly = list(s_lib.splines_x), list(s_lib.splines_y)
        # mirror our phantom-endpoint dedup on the library arrays
        if len(lx) >= 2 and lx[0] == lx[1] and ly[0] == ly[1]:
            lx, ly = lx[1:], ly[1:]
        if len(lx) >= 2 and lx[-1] == lx[-2] and ly[-1] == ly[-2]:
            lx, ly = lx[:-1], ly[:-1]
        assert s_ir.x == pytest.approx(lx, abs=1e-4)
        assert s_ir.y == pytest.approx(ly, abs=1e-4)


def test_oracle_corpus_sample():
    """Same oracle over a real corpus file (compressed splines)."""
    uim_parser = pytest.importorskip(
        "uim.codec.parser.uim", reason="universal-ink-library not installed")
    sample = CORPUS / "uim_3.1.0" / "6) Different Input Providers.uim"
    if not sample.is_file():
        pytest.skip("corpus samples not present")
    model = uim_parser.UIMParser().parse(str(sample))
    ours = list(UimReader().read(sample).pages[0].strokes())
    assert len(ours) == len(model.strokes)
    for s_ir, s_lib in zip(ours, model.strokes):
        lx, ly = list(s_lib.splines_x), list(s_lib.splines_y)
        if len(lx) >= 2 and lx[0] == lx[1] and ly[0] == ly[1]:
            lx, ly = lx[1:], ly[1:]
        if len(lx) >= 2 and lx[-1] == lx[-2] and ly[-1] == ly[-2]:
            lx, ly = lx[:-1], ly[:-1]
        assert s_ir.x == pytest.approx(lx, abs=1e-4)
        assert s_ir.y == pytest.approx(ly, abs=1e-4)
