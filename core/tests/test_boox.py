"""Onyx Boox .note reader: wire units, detect discrimination, fixture +
corpus integration.

Corpus tests use third-party samples from the gitignored corpus/ dir
(boox-note-optimizer's demo.note / empty.note, MIT); they skip cleanly
when the corpus is absent. The committed fixture is self-made
(tests/fixtures/boox/make_fixture.py, CC0).
"""
from __future__ import annotations

import math
import struct
import zipfile
from pathlib import Path

import pytest

from inkterop import ir
from inkterop.formats.base import Fidelity
from inkterop.formats.boox import (
    BooxReader,
    RawPoint,
    ShapeMeta,
    _ink_stroke,
    parse_points_blob,
)

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "boox" / "boox-synthetic.note"


def _find_corpus() -> Path:
    """corpus/ lives at the repo root, gitignored; in linked worktrees it
    only exists in the main checkout, so walk up the ancestors."""
    for base in Path(__file__).resolve().parents[2:]:
        candidate = base / "corpus" / "third-party" / "boox-note-optimizer"
        if candidate.is_dir():
            return candidate
    return Path(__file__).parents[2] / "corpus" / "third-party" / \
        "boox-note-optimizer"


CORPUS = _find_corpus()
needs_corpus = pytest.mark.skipif(
    not CORPUS.exists(), reason="third-party corpus not present"
)


# ---------------------------------------------------------------- units

def _synthetic_blob(strokes: list[tuple[str, list[tuple]]]) -> bytes:
    out = bytearray()
    out += struct.pack(">I", 1)
    out += b"11112222333344445555666677778888".ljust(36)
    out += b"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    index = []
    for uuid, pts in strokes:
        offset = len(out)
        out += b"\x00\x00\x00\x00"
        for p in pts:
            out += struct.pack(">ffBBHI", *p)
        index.append((uuid, offset, len(out) - offset))
    index_start = len(out)
    for uuid, offset, size in index:
        out += uuid.encode("ascii") + struct.pack(">II", offset, size)
    out += struct.pack(">I", index_start)
    return bytes(out)


def test_parse_points_blob_synthetic():
    u1 = "10000000-0000-4000-8000-00000000aaaa"
    u2 = "10000000-0000-4000-8000-00000000bbbb"
    blob = _synthetic_blob([
        (u1, [(10.5, 20.25, 3, 4, 1000, 0), (11.0, 21.0, 3, 5, 2000, 7)]),
        (u2, [(500.0, 600.0, 0, 0, 4095, 0)]),
    ])
    strokes = parse_points_blob(blob)
    assert set(strokes) == {u1, u2}
    p0, p1 = strokes[u1]
    assert (p0.x, p0.y) == (10.5, 20.25)
    assert (p0.tilt_x, p0.tilt_y, p0.pressure, p0.t_ms) == (3, 4, 1000, 0)
    assert (p1.pressure, p1.t_ms) == (2000, 7)
    assert strokes[u2][0].pressure == 4095


def test_parse_points_blob_big_endian():
    # x=1.0 must decode from BE bytes 3f800000, not LE
    u = "10000000-0000-4000-8000-00000000cccc"
    blob = _synthetic_blob([(u, [(1.0, 2.0, 0, 0, 0x0102, 0x01020304)])])
    rec_off = blob.index(b"\x00\x00\x00\x00", 76) + 4
    assert blob[rec_off:rec_off + 4] == b"\x3f\x80\x00\x00"
    p = parse_points_blob(blob)[u][0]
    assert (p.x, p.y, p.pressure, p.t_ms) == (1.0, 2.0, 0x0102, 0x01020304)


def test_parse_points_blob_empty():
    # header + index pointer only (real empty.note layout: 80 bytes)
    blob = _synthetic_blob([])
    assert len(blob) == 80
    assert parse_points_blob(blob) == {}


def test_parse_points_blob_rejects_garbage():
    from inkterop.formats.boox import WireError
    with pytest.raises(WireError):
        parse_points_blob(b"\x00" * 20)
    # index pointer past end
    bad = _synthetic_blob([])[:-4] + struct.pack(">I", 10_000)
    with pytest.raises(WireError):
        parse_points_blob(bad)


def test_ink_stroke_width_formulas():
    pts = [RawPoint(0.0, 0.0, 0, 0, 4095, 0),
           RawPoint(10.0, 0.0, 0, 0, 1024, 5)]
    fountain = _ink_stroke(
        ShapeMeta(uuid="u", page_id="p", pen_type=5, thickness=6.0), pts)
    w = fountain.channels[ir.Channel.WIDTH]
    assert w[0] == pytest.approx(6.0 * 1.37)  # full pressure
    assert w[1] == pytest.approx(6.0 * 1.37 * (1024 / 4095) ** 0.59)
    assert fountain.appearance.mode is ir.GeometryMode.STROKED_VARIABLE
    assert fountain.channels[ir.Channel.PRESSURE][0] == pytest.approx(1.0)
    assert fountain.channels[ir.Channel.TIMESTAMP] == [0.0, 0.005]

    ball = _ink_stroke(
        ShapeMeta(uuid="u", page_id="p", pen_type=2, thickness=4.0), pts)
    assert ball.channels[ir.Channel.WIDTH] == [4.0, 4.0]
    assert ball.appearance.mode is ir.GeometryMode.STROKED_CONSTANT

    hl = _ink_stroke(
        ShapeMeta(uuid="u", page_id="p", pen_type=15, thickness=59.0), pts)
    assert hl.appearance.underlay is True
    assert hl.appearance.blend is ir.BlendMode.MULTIPLY
    assert hl.appearance.opacity == pytest.approx(0.5)


def test_ink_stroke_transform_and_tilt():
    pts = [RawPoint(10.0, 20.0, 64, 7, 2048, 0)]
    meta = ShapeMeta(uuid="u", page_id="p", pen_type=2, thickness=4.0,
                     matrix=(2.0, 0.0, 100.0, 0.0, 2.0, 200.0))
    s = _ink_stroke(meta, pts)
    assert (s.x[0], s.y[0]) == (120.0, 240.0)
    # thickness scales with the matrix
    assert s.channels[ir.Channel.WIDTH][0] == pytest.approx(8.0)
    # tilt_x 64/256 of a turn -> pi/2 [inferred mapping]
    assert s.channels[ir.Channel.TILT_AZIMUTH][0] == pytest.approx(math.pi / 2)
    assert s.extra["boox"]["tilt_y"] == [7]


def test_sign_extended_color():
    # device emits ARGB as sign-extended int64 varint: black 0xFF000000
    # arrives as 18446744073692774400 (observed in demo.note)
    def varint(v: int) -> bytes:
        out = bytearray()
        while True:
            b, v = v & 0x7F, v >> 7
            out.append(b | 0x80 if v else b)
            if not v:
                return bytes(out)

    msg = (b"\x0a\x24" + b"10000000-0000-4000-8000-00000000dddd"
           + b"\x20" + varint(18446744073692774400))
    from inkterop.formats.boox import parse_shape_message
    meta = parse_shape_message(msg, "p")
    assert meta.argb == 0xFF000000


# ---------------------------------------------------------------- detect

def test_detect_fixture():
    assert BooxReader().detect(FIXTURE)


def test_detect_rejects_other_note_formats():
    reader = BooxReader()
    # Supernote also claims .note (binary container)
    assert not reader.detect(
        FIXTURES / "supernote" / "synthetic-two-page.note")
    assert not reader.detect(
        FIXTURES / "supernote" / "synthetic-landscape.note")
    # Notability zips (Session.plist / .ntb) must not match either
    assert not reader.detect(FIXTURES / "notability" / "scribbles.ntb")
    # other zip containers
    assert not reader.detect(
        FIXTURES / "goodnotes" / "gn-mac-mixed-pens.goodnotes")
    assert not reader.detect(FIXTURES / "saber" / "saber-mac-pens-text.sba")


def test_other_readers_reject_boox():
    from inkterop.formats.notability import NotabilityReader
    from inkterop.formats.supernote import SupernoteReader
    assert not SupernoteReader().detect(FIXTURE)
    assert not NotabilityReader().detect(FIXTURE)


def test_registry_routing_unchanged():
    """Existing .note claimants still resolve their own fixtures."""
    from inkterop.formats import reader_for
    r = reader_for(FIXTURES / "supernote" / "synthetic-two-page.note")
    assert r is not None and r.format_id == "supernote"
    r = reader_for(FIXTURES / "notability" / "scribbles.ntb")
    assert r is not None and r.format_id.startswith("notability")


# ---------------------------------------------------------------- fixture

def test_read_fixture():
    doc = BooxReader().read(FIXTURE)
    doc.validate()
    assert doc.title == "boox synthetic"
    assert len(doc.pages) == 1
    page = doc.pages[0]
    assert page.bounds.width == pytest.approx(1860.0)
    assert page.bounds.height == pytest.approx(2480.0)
    assert page.point_scale == 1.0

    strokes = list(page.strokes())
    assert [s.tool.family.value for s in strokes] == \
        ["ballpoint", "pen", "highlighter"]  # created-timestamp order

    fountain = strokes[1]
    pressures = fountain.channels[ir.Channel.PRESSURE]
    assert len(pressures) == len(fountain.x) == 20
    assert 0.0 < min(pressures) < max(pressures) <= 1.0
    widths = fountain.channels[ir.Channel.WIDTH]
    assert widths[0] < widths[-1]  # pressure-driven variable width

    hl = strokes[2]
    assert hl.appearance.underlay is True
    assert hl.y[0] == pytest.approx(600.0)  # transform matrix applied

    texts = [t for layer in page.layers for t in layer.texts]
    assert [t.text for t in texts] == ["synthetic boox"]
    assert texts[0].x == pytest.approx(100.0)

    b = page.bounds
    for s in strokes:
        assert all(b.x_min <= x <= b.x_max for x in s.x)
        assert all(b.y_min <= y <= b.y_max for y in s.y)


def test_fixture_to_pdf_and_svg(tmp_path):
    from inkterop.render.pdf import PdfWriter
    from inkterop.render.svg import SvgWriter

    doc = BooxReader().read(FIXTURE)
    pdf = tmp_path / "boox.pdf"
    PdfWriter().write(doc, pdf, Fidelity.EXACT)
    assert pdf.read_bytes()[:5] == b"%PDF-"
    svg = tmp_path / "boox.svg"
    SvgWriter().write(doc, svg, Fidelity.EXACT)
    assert svg.read_text().count("<path") >= 3


# ---------------------------------------------------------------- corpus

@needs_corpus
def test_corpus_demo_note():
    doc = BooxReader().read(CORPUS / "web" / "demo.note")
    doc.validate()
    assert doc.title == "lines"
    assert len(doc.pages) == 1
    page = doc.pages[0]
    assert page.bounds.width == pytest.approx(1860.0)
    assert page.bounds.height == pytest.approx(2480.0)

    strokes = list(page.strokes())
    assert len(strokes) == 9
    assert sum(len(s) for s in strokes) == 8179
    pens = sorted(s.tool.native.tool_id for s in strokes)
    assert pens == [2, 2, 5, 15, 21, 22, 22, 60, 61]

    b = page.bounds
    for s in strokes:
        assert all(b.x_min <= x <= b.x_max for x in s.x)
        assert all(b.y_min <= y <= b.y_max for y in s.y)
        assert all(0.0 <= p <= 1.0
                   for p in s.channels[ir.Channel.PRESSURE])
        ts = s.channels[ir.Channel.TIMESTAMP]
        assert ts == sorted(ts)  # cumulative, monotonic

    hl = next(s for s in strokes if s.tool.native.tool_id == 15)
    assert hl.tool.family is ir.ToolFamily.HIGHLIGHTER
    assert hl.appearance.mode is ir.GeometryMode.STROKED_CONSTANT
    fountain = next(s for s in strokes if s.tool.native.tool_id == 5)
    assert fountain.appearance.mode is ir.GeometryMode.STROKED_VARIABLE


@needs_corpus
def test_corpus_empty_note():
    doc = BooxReader().read(CORPUS / "web" / "empty.note")
    doc.validate()
    assert len(doc.pages) == 1
    assert sum(len(list(p.strokes())) for p in doc.pages) == 0


@needs_corpus
def test_corpus_detect_both_samples():
    reader = BooxReader()
    assert reader.detect(CORPUS / "web" / "demo.note")
    assert reader.detect(CORPUS / "web" / "empty.note")


@needs_corpus
def test_corpus_demo_to_pdf(tmp_path):
    from inkterop.render.pdf import PdfWriter

    doc = BooxReader().read(CORPUS / "web" / "demo.note")
    out = tmp_path / "demo.pdf"
    PdfWriter().write(doc, out, Fidelity.EXACT)
    data = out.read_bytes()
    assert data[:5] == b"%PDF-"
    assert len(data) > 10_000  # real ink, not an empty page
