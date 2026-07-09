"""Samsung Notes (.sdocx) reader tests.

Unit tests parse synthetic stroke records built by the fixture
generator (tests/fixtures/sdocx/make_fixture.py — an independent
encoder, so the pair is not a closed loop only where byte layouts are
additionally pinned by hand below). Integration tests use third-party
samples from the gitignored corpus/ dir (twangodev/sdocx samples;
GPL repo, but samples are study data only — never committed).
"""
from __future__ import annotations

import importlib.util
import math
import struct
from pathlib import Path

import pytest

from inkterop import ir
from inkterop.formats.base import Fidelity
from inkterop.formats.sdocx import (
    SdocxError,
    SdocxReader,
    fixed_point_delta,
    fixed_small_delta,
    parse_stroke,
    read_note_strings,
    read_page_list,
)

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "sdocx" / "synthetic-two-page.sdocx"


def _load_maker():
    spec = importlib.util.spec_from_file_location(
        "sdocx_make_fixture", FIXTURES / "sdocx" / "make_fixture.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


maker = _load_maker()


def _find_corpus() -> Path:
    """corpus/ lives at the repo root, gitignored; in linked worktrees it
    only exists in the main checkout, so walk up the ancestors."""
    for base in Path(__file__).resolve().parents[2:]:
        candidate = base / "corpus" / "third-party" / "twangodev-sdocx" / \
            "samples"
        if candidate.is_dir():
            return candidate
    return Path(__file__).parents[2] / "corpus" / "third-party" / \
        "twangodev-sdocx" / "samples"


CORPUS = _find_corpus()
needs_corpus = pytest.mark.skipif(
    not CORPUS.is_dir(), reason="third-party corpus not present")


# ------------------------------------------------------------ wire primitives

def test_fixed_point_delta():
    # sign | 10-bit integer | 5-bit fraction (hand-computed)
    assert fixed_point_delta(0x0000) == 0.0
    assert fixed_point_delta(0x0020) == 1.0  # integer 1, fraction 0
    assert fixed_point_delta(0x0001) == 1 / 32
    assert fixed_point_delta(0x8021) == -(1.0 + 1 / 32)
    assert fixed_point_delta(0x7FFF) == 1023 + 31 / 32


def test_fixed_small_delta():
    # sign | 3-bit integer | 12-bit fraction (hand-computed)
    assert fixed_small_delta(0x0000) == 0.0
    assert fixed_small_delta(0x1000) == 1.0
    assert fixed_small_delta(0x0001) == 1 / 4096
    assert fixed_small_delta(0x9001) == -(1.0 + 1 / 4096)
    assert fixed_small_delta(0x7FFF) == 7 + 4095 / 4096


def test_fixed_point_codec_round_trip():
    for v in (0.0, 0.5, -0.5, 12.0 + 5 / 32, -1023.0):
        assert fixed_point_delta(maker.encode_point_delta(v)) == v
    for v in (0.0, 1 / 4096, -3.5, 7.0):
        assert fixed_small_delta(maker.encode_small_delta(v)) == v


# ----------------------------------------------------- stroke record parsing

STRINGS = {7: maker.PEN + "FountainPen"}


def _stroke_bytes(**kw) -> bytes:
    defaults = dict(
        points=[(10.0, 20.0), (11.0, 21.5)],
        pressures=[0.25, 0.75],
        timestamps=[0, 10],
        pen_name_id=7,
        color_bgra=bytes([0x10, 0x20, 0x30, 0xFF]),
        pen_size=5.0,
    )
    defaults.update(kw)
    payload = maker.stroke_object("test-uuid", **defaults)
    return payload[:-32]  # reader receives the payload minus object hash


def test_parse_stroke_uncompressed():
    rs = parse_stroke(_stroke_bytes(), STRINGS)
    assert rs.x == [10.0, 11.0]
    assert rs.y == [20.0, 21.5]
    assert rs.pressure == [0.25, 0.75]
    assert rs.timestamp == [0, 10]
    assert rs.tilt is None
    assert rs.pen_name == maker.PEN + "FountainPen"
    assert rs.color_bgra == bytes([0x10, 0x20, 0x30, 0xFF])
    assert rs.pen_size == 5.0
    assert rs.tool_type == 2  # S-Pen


def test_parse_stroke_compressed_with_tilt():
    rs = parse_stroke(_stroke_bytes(
        points=[(10.0, 20.0), (11.0, 19.5), (12.5, 19.5)],
        pressures=[0.5, 0.75, 0.5],
        timestamps=[100, 108, 120],
        tilt=[0.25, 0.5, 0.5],
        orientation=[-1.0, -1.25, -1.0],
        compressed=True,
    ), STRINGS)
    assert rs.x == pytest.approx([10.0, 11.0, 12.5])
    assert rs.y == pytest.approx([20.0, 19.5, 19.5])
    assert rs.pressure == pytest.approx([0.5, 0.75, 0.5])
    assert rs.timestamp == [100, 108, 120]
    assert rs.tilt == pytest.approx([0.25, 0.5, 0.5])
    assert rs.orientation == pytest.approx([-1.0, -1.25, -1.0])


def test_parse_stroke_single_dot():
    rs = parse_stroke(_stroke_bytes(
        points=[(1.0, 2.0)], pressures=[0.9], timestamps=[0]), STRINGS)
    assert (rs.x, rs.y) == ([1.0], [2.0])
    assert rs.pressure == pytest.approx([0.9])  # stored as f32


def test_parse_stroke_unknown_pen_id():
    rs = parse_stroke(_stroke_bytes(pen_name_id=999), STRINGS)
    assert rs.pen_name == ""  # missing registry entry degrades gracefully


def test_parse_stroke_rejects_unknown_field_bits():
    payload = bytearray(_stroke_bytes())
    # The stroke frame starts right after the ObjectBase frame; flip an
    # un-parseable field-flag bit (bit 0) in its header.
    base_size = struct.unpack_from("<I", payload, 0)[0]
    w = base_size  # stroke frame offset
    # frame: size u32, type u16, flex u32, prop bitfield, then field bitfield
    p = w + 4 + 2 + 4
    p += 1 + payload[p]  # skip property bitfield
    assert payload[p] >= 1  # field bitfield has at least one byte
    payload[p + 1] |= 0x01
    with pytest.raises(SdocxError, match="unknown stroke field"):
        parse_stroke(bytes(payload), STRINGS)


def test_page_list_and_note_strings():
    note = maker.note_note(100, 200, {3: "abc", 9: "déf"})
    strings, w, h = read_note_strings(note)
    assert (w, h) == (100, 200)
    assert strings == {3: "abc", 9: "déf"}

    page = maker.page_member("u-1", 100, 200, [maker.layer([])])
    info = maker.page_id_info(note, [page], ["u-1"])
    assert read_page_list(info) == ["u-1"]


# ------------------------------------------------------------------- detect

def test_detect_discrimination():
    reader = SdocxReader()
    assert reader.detect(FIXTURE)
    # Other zip containers must be rejected by the member sniff — the
    # .note extension is already ambiguous (Supernote binary vs
    # Notability zip), and goodnotes/.sba are zips too.
    for other in [
        FIXTURES / "goodnotes" / "gn-mac-mixed-pens.goodnotes",
        FIXTURES / "saber" / "saber-mac-pens-text.sba",
        FIXTURES / "supernote" / "synthetic-two-page.note",
        FIXTURES / "notability" / "scribbles.ntb",
        FIXTURES / "remarkable" / "ballpoint-small.rm",
    ]:
        assert not reader.detect(other), other.name


# ----------------------------------------------------------- fixture reading

def test_read_fixture():
    doc = SdocxReader().read(FIXTURE)
    doc.validate()
    assert doc.metadata["sdocx_page_model"] == "paged"
    assert len(doc.pages) == 2

    p0 = doc.pages[0]
    assert (p0.bounds.width, p0.bounds.height) == (1440.0, 2038.0)
    assert p0.point_scale == pytest.approx(595.276 / 1440.0)
    assert p0.layers[0].name == "Layer 1"

    strokes = list(p0.strokes())
    assert sorted(s.tool.family.value for s in strokes) == \
        ["highlighter", "pen"]

    pen = next(s for s in strokes if s.tool.family is ir.ToolFamily.PEN)
    assert pen.x == [100.0, 150.0, 220.0]
    assert pen.channels[ir.Channel.PRESSURE] == \
        pytest.approx([0.3, 0.8, 0.5])  # stored as f32
    assert pen.channels[ir.Channel.TIMESTAMP] == \
        pytest.approx([0.0, 0.012, 0.025])
    assert pen.channels[ir.Channel.TILT_ALTITUDE] == \
        pytest.approx([math.pi / 2 - t for t in (0.4, 0.45, 0.5)])
    # colour bytes are BGRA
    assert (pen.color.r, pen.color.g, pen.color.b) == \
        (0x10 / 255, 0x20 / 255, 0x30 / 255)
    # pressure-sensitive: per-point WIDTH from the clamped-pressure model
    assert pen.appearance.mode is ir.GeometryMode.STROKED_VARIABLE
    assert pen.channels[ir.Channel.WIDTH] == \
        pytest.approx([12.0 * 0.4, 12.0 * 0.7, 12.0 * 0.5], rel=1e-6)

    hl = next(s for s in strokes if s.tool.family is ir.ToolFamily.HIGHLIGHTER)
    assert hl.appearance.underlay is True
    assert hl.appearance.blend is ir.BlendMode.DARKEN
    assert hl.appearance.opacity == pytest.approx(0x59 / 255)
    assert hl.appearance.mode is ir.GeometryMode.STROKED_CONSTANT
    assert hl.appearance.width == pytest.approx(20.0 * 2.5 * 0.45)

    # page 2: compressed stroke decodes exactly; dot survives
    p1 = list(doc.pages[1].strokes())
    pencil = next(s for s in p1 if s.tool.family is ir.ToolFamily.PENCIL)
    assert pencil.x == pytest.approx([300.0, 302.5, 306.25])
    assert pencil.y == pytest.approx([300.0, 298.0, 297.5])
    assert any(len(s) == 1 for s in p1)


def test_fixture_generator_is_pinned():
    """The committed fixture matches the generator byte-for-byte."""
    assert FIXTURE.read_bytes() == maker.build_fixture()


def test_fixture_to_pdf(tmp_path):
    from inkterop.render.pdf import PdfWriter

    doc = SdocxReader().read(FIXTURE)
    out = tmp_path / "sdocx.pdf"
    PdfWriter().write(doc, out, Fidelity.EXACT)
    assert out.read_bytes()[:5] == b"%PDF-"


# --------------------------------------------------------- corpus integration

def _corpus_files() -> list[Path]:
    return sorted(CORPUS.glob("*.sdocx")) if CORPUS.is_dir() else []


@needs_corpus
@pytest.mark.parametrize("path", _corpus_files(), ids=lambda p: p.stem)
def test_corpus_parses_and_validates(path: Path):
    reader = SdocxReader()
    assert reader.detect(path)
    doc = reader.read(path)
    doc.validate()
    assert doc.pages
    for page in doc.pages:
        b = page.bounds
        assert b.width > 0 and b.height > 0
        for s in page.strokes():
            assert all(b.x_min <= x <= b.x_max for x in s.x)
            assert all(b.y_min <= y <= b.y_max for y in s.y)
            pressures = s.channels[ir.Channel.PRESSURE]
            assert all(0.0 <= p <= 1.0 for p in pressures)


@needs_corpus
def test_corpus_handwritten_strokes():
    doc = SdocxReader().read(CORPUS / "handwritten.sdocx")
    strokes = [s for page in doc.pages for s in page.strokes()]
    assert len(strokes) > 100  # densely handwritten notes
    assert all(s.tool.family is ir.ToolFamily.PEN for s in strokes)
    assert all(
        str(s.tool.native.tool_id).endswith("FountainPen") for s in strokes)
    # tilt is recorded by the S-Pen
    assert all(ir.Channel.TILT_ALTITUDE in s.channels for s in strokes)


@needs_corpus
def test_corpus_to_pdf_smoke(tmp_path):
    from inkterop.render.pdf import PdfWriter

    doc = SdocxReader().read(CORPUS / "handwritten.sdocx")
    out = tmp_path / "handwritten.pdf"
    PdfWriter().write(doc, out, Fidelity.EXACT)
    data = out.read_bytes()
    assert data[:5] == b"%PDF-"
    assert len(data) > 100_000  # thousands of strokes -> real drawing ops
