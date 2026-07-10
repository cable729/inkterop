"""GoodNotes writer: encoder round-trip properties + write->read fidelity."""
from __future__ import annotations

import struct
import zipfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from inkterop import formats, ir
from inkterop.formats.base import Fidelity
from inkterop.formats.goodnotes import GoodnotesReader, GoodnotesWriter
from inkterop.formats.goodnotes.reader import extract_path, parse_tpl
from inkterop.formats.goodnotes.wire import (
    apple_lz4_compress,
    apple_lz4_decompress,
    encode_tpl,
    fields_by_number,
    join_delimited,
    read_varint,
    split_delimited,
    write_float32,
    write_len_delimited,
    write_varint,
    write_varint_field,
)
from inkterop.formats.goodnotes.writer import document_to_goodnotes

FIXTURE = Path(__file__).parent / "fixtures" / "goodnotes" / \
    "gn-mac-mixed-pens.goodnotes"


def _ensure_registered() -> None:
    if not any(w.format_id == "goodnotes" for w in formats.writers()):
        formats.register_writer(GoodnotesWriter())


# --- protobuf wire encoders ---------------------------------------------------

@given(st.integers(min_value=0, max_value=2**63 - 1))
def test_varint_round_trip(n):
    buf = write_varint(n)
    value, pos = read_varint(buf, 0)
    assert value == n
    assert pos == len(buf)


def test_field_encoders_round_trip():
    msg = (write_varint_field(1, 150)
           + write_float32(2, 1.5)
           + write_len_delimited(3, b"hi")
           + write_len_delimited(4, b""))
    fields = fields_by_number(msg)
    assert fields[1][0].value == 150
    assert fields[1][0].wire_type == 0
    assert fields[2][0].value == pytest.approx(1.5)
    assert fields[2][0].wire_type == 5
    assert fields[3][0].value == b"hi"
    assert fields[4][0].value == b""


@given(st.lists(st.binary(max_size=300), max_size=8))
def test_join_delimited_round_trip(records):
    assert split_delimited(join_delimited(records)) == records


# --- Apple LZ4 frame encoder --------------------------------------------------

@given(st.binary(max_size=2000))
def test_apple_lz4_round_trip(data):
    framed = apple_lz4_compress(data)
    assert framed.endswith(b"bv4$")
    assert apple_lz4_decompress(framed) == data


@pytest.mark.parametrize("size", [(1 << 16) - 1, 1 << 16, (1 << 16) + 1,
                                  3 * (1 << 16) + 7])
def test_apple_lz4_block_boundary(size):
    """Payloads around the 64 KiB raw-block cap split into legal frames."""
    data = bytes(i & 0xFF for i in range(size))
    framed = apple_lz4_compress(data)
    n_blocks = framed.count(b"bv4-")
    assert n_blocks == -(-size // (1 << 16))  # every block <= 64 KiB
    assert apple_lz4_decompress(framed) == data


def test_apple_lz4_empty():
    assert apple_lz4_compress(b"") == b"bv4$"
    assert apple_lz4_decompress(b"bv4$") == b""


# --- tpl encoder ----------------------------------------------------------------

_f32 = st.floats(width=32, allow_nan=False, allow_infinity=False)
_u16 = st.integers(min_value=0, max_value=0xFFFF)

_scalar = st.one_of(
    st.tuples(st.just("scalar"), st.just("v"),
              st.lists(_u16, min_size=1, max_size=1)),
    st.tuples(st.just("scalar"), st.just("u"),
              st.lists(_f32, min_size=1, max_size=1)),
)
_array = st.one_of(
    st.tuples(st.just("array"), st.just("v"), st.lists(_u16, max_size=12)),
    st.tuples(st.just("array"), st.just("u"), st.lists(_f32, max_size=12)),
)
_struct_array = st.integers(min_value=1, max_value=4).flatmap(
    lambda k: st.tuples(
        st.just("struct_array"), st.just("u" * k),
        st.lists(st.tuples(*[_f32] * k), max_size=8),
    )
)
_sections = st.lists(st.one_of(_scalar, _array, _struct_array), max_size=8)


@given(_sections)
def test_encode_tpl_round_trip(sections):
    sections = [(k, s, list(v)) for k, s, v in sections]
    blob = encode_tpl(sections)
    parsed = parse_tpl(blob)  # raises on any residue
    assert len(parsed) == len(sections)
    for (k1, s1, v1), (k2, s2, v2) in zip(sections, parsed):
        assert (k1, s1) == (k2, s2)
        assert v2 == pytest.approx(v1)


def test_encode_tpl_total_length_and_header():
    blob = encode_tpl([("scalar", "v", [2])])
    assert blob[:4] == b"tpl\x00"
    assert struct.unpack_from("<I", blob, 4)[0] == len(blob)


def test_encoded_geometry_extracts_as_triplet_path():
    from inkterop.formats.goodnotes.writer import _geometry_blob

    pts = [(10.0, 20.0, 1.5), (30.0, 40.0, 2.0), (50.0, 60.0, 1.0)]
    path, constant = extract_path(_geometry_blob(pts))
    assert constant is False
    assert path == [pytest.approx(t) for t in pts]


# --- synthetic IR write -> read -----------------------------------------------

def _synthetic_doc() -> ir.Document:
    pen = ir.Stroke(  # variable width, appearance.width None (rM-style)
        x=[10.0, 60.0, 110.0, 160.0], y=[20.0, 25.0, 20.0, 30.0],
        tool=ir.ToolRef(family=ir.ToolFamily.PEN),
        color=ir.Color(0.0, 0.0, 1.0),
        channels={ir.Channel.WIDTH: [1.0, 2.5, 3.0, 1.5]},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_VARIABLE, width=None,
            color=ir.Color(0.0, 0.0, 1.0), opacity=1.0,
        ),
    )
    hl = ir.Stroke(
        x=[10.0, 110.0, 210.0], y=[50.0, 50.0, 50.0],
        tool=ir.ToolRef(family=ir.ToolFamily.HIGHLIGHTER),
        color=ir.Color(1.0, 1.0, 0.0),
        channels={ir.Channel.WIDTH: [20.0, 20.0, 20.0]},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT, width=20.0,
            color=ir.Color(1.0, 1.0, 0.0), opacity=0.5,
            underlay=True, blend=ir.BlendMode.DARKEN,
        ),
    )
    dot = ir.Stroke(  # single point: writer must pad to a readable path
        x=[200.0], y=[200.0],
        tool=ir.ToolRef(family=ir.ToolFamily.PENCIL),
        color=ir.Color(0.2, 0.2, 0.2),
        channels={ir.Channel.WIDTH: [3.0]},
    )
    pages = [
        ir.Page(bounds=ir.Rect(0.0, 0.0, 595.28, 841.89), point_scale=1.0,
                layers=[ir.Layer(strokes=[pen, hl])]),
        ir.Page(bounds=ir.Rect(0.0, 0.0, 595.28, 841.89), point_scale=1.0,
                layers=[ir.Layer(strokes=[dot])]),
    ]
    return ir.Document(format_id="test", title="synthetic", pages=pages)


def test_writer_synthetic_round_trip(tmp_path):
    src = _synthetic_doc()
    out = tmp_path / "out.goodnotes"
    GoodnotesWriter().write(src, out, Fidelity.EXACT)

    reader = GoodnotesReader()
    assert reader.detect(out)
    back = reader.read(out)
    back.validate()
    assert len(back.pages) == 2

    p0 = list(back.pages[0].strokes())
    assert len(p0) == 2
    pen, hl = p0  # record order preserved

    assert pen.tool.family is ir.ToolFamily.PEN
    assert pen.x == pytest.approx([10.0, 60.0, 110.0, 160.0], abs=0.01)
    assert pen.y == pytest.approx([20.0, 25.0, 20.0, 30.0], abs=0.01)
    assert pen.channels[ir.Channel.WIDTH] == pytest.approx(
        [1.0, 2.5, 3.0, 1.5], abs=0.01)
    assert pen.color.rgb() == pytest.approx((0.0, 0.0, 1.0))

    assert hl.tool.family is ir.ToolFamily.HIGHLIGHTER
    assert hl.tool.native.tool_id == "highlighter"
    assert hl.appearance.underlay is True
    assert hl.appearance.opacity == pytest.approx(0.5, abs=1 / 255)
    assert hl.channels[ir.Channel.WIDTH] == pytest.approx([20.0] * 3)

    # dot on page 2 survives (padded to >= 3 identical triplet points)
    p1 = list(back.pages[1].strokes())
    assert len(p1) == 1
    assert p1[0].tool.native.tool_id == "pencil"  # field 3 = 5 on the wire
    assert p1[0].tool.family is ir.ToolFamily.PENCIL
    assert all(x == pytest.approx(200.0) for x in p1[0].x)
    assert all(y == pytest.approx(200.0) for y in p1[0].y)


def test_writer_native_fidelity_constant_widths(tmp_path):
    out = tmp_path / "native.goodnotes"
    GoodnotesWriter().write(_synthetic_doc(), out, Fidelity.NATIVE)
    back = GoodnotesReader().read(out)
    p0 = list(back.pages[0].strokes())
    pen, hl = p0
    assert pen.channels[ir.Channel.WIDTH] == pytest.approx([1.56] * len(pen))
    assert hl.channels[ir.Channel.WIDTH] == pytest.approx([24.0] * len(hl))


def test_writer_raw_fidelity_rejected():
    with pytest.raises(ValueError, match="raw"):
        document_to_goodnotes(_synthetic_doc(), Fidelity.RAW)


def test_writer_container_members(tmp_path):
    out = tmp_path / "members.goodnotes"
    GoodnotesWriter().write(_synthetic_doc(), out, Fidelity.EXACT)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "schema.pb" in names
        assert "index.notes.pb" in names
        assert "thumbnail.jpg" in names
        pages = [n for n in names if n.startswith("notes/")]
        assert len(pages) == 2
        assert zf.read("schema.pb") == b"\x08\x18"  # field 1 varint 24
        assert zf.read("thumbnail.jpg")[:2] == b"\xff\xd8"  # JPEG SOI


def test_reader_materializes_page_from_events(tmp_path):
    """A page whose notes/ blob is missing still exists: the reader
    replays it from index.events.pb (page-created event + paper size)."""
    pen = ir.Stroke(
        x=[10.0, 60.0, 110.0], y=[20.0, 25.0, 20.0],
        tool=ir.ToolRef(family=ir.ToolFamily.PEN),
        color=ir.Color(0.0, 0.0, 0.0),
        channels={ir.Channel.WIDTH: [1.0, 2.0, 1.5]},
    )
    src = ir.Document(format_id="test", title="events", pages=[
        ir.Page(bounds=ir.Rect(0.0, 0.0, 700.0, 900.0), point_scale=1.0,
                layers=[ir.Layer(strokes=[pen])]),
        ir.Page(bounds=ir.Rect(0.0, 0.0, 700.0, 900.0), point_scale=1.0,
                layers=[ir.Layer(strokes=[])]),
    ])
    out = tmp_path / "events.goodnotes"
    GoodnotesWriter().write(src, out, Fidelity.EXACT)

    with zipfile.ZipFile(out) as zf:
        page_uuids = [n.removeprefix("notes/") for n in zf.namelist()
                      if n.startswith("notes/")]
        members = {n: zf.read(n) for n in zf.namelist()}
    assert len(page_uuids) == 2
    del members[f"notes/{page_uuids[1]}"]
    gutted = tmp_path / "gutted.goodnotes"
    with zipfile.ZipFile(gutted, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)

    back = GoodnotesReader().read(gutted)
    assert len(back.pages) == 2
    assert len(list(back.pages[0].strokes())) == 1
    p2 = back.pages[1]
    assert p2.extra["goodnotes"]["page_uuid"] == page_uuids[1]
    assert not list(p2.strokes())
    # paper size replayed from the events journal, not the A4 fallback
    assert p2.bounds.width == pytest.approx(700.0, abs=0.01)
    assert p2.bounds.height == pytest.approx(900.0, abs=0.01)


# --- fixture round-trip ---------------------------------------------------------

def test_writer_fixture_round_trip(tmp_path):
    src = GoodnotesReader().read(FIXTURE)
    out = tmp_path / "rt.goodnotes"
    GoodnotesWriter().write(src, out, Fidelity.EXACT)

    back = GoodnotesReader().read(out)
    back.validate()
    assert len(back.pages) == len(src.pages)
    for sp, bp in zip(src.pages, back.pages):
        ss, bs = list(sp.strokes()), list(bp.strokes())
        # Constant-width/pencil/brush records re-emit as plain triplets;
        # stroke COUNT is preserved, byte layout is not.
        assert len(bs) == len(ss)
        for a, b in zip(ss, bs):
            assert b.tool.native.tool_id == a.tool.native.tool_id
            assert b.tool.family is a.tool.family
            assert len(b) == len(a)
            assert b.x == pytest.approx(a.x, abs=0.5)
            assert b.y == pytest.approx(a.y, abs=0.5)
            assert b.channels[ir.Channel.WIDTH] == pytest.approx(
                a.channels[ir.Channel.WIDTH], abs=0.05)
            assert b.color.rgb() == pytest.approx(a.color.rgb(), abs=1e-3)
            assert b.appearance.opacity == pytest.approx(
                a.appearance.opacity, abs=1 / 255)


# --- convert integration ---------------------------------------------------------

def test_writer_experimental_gate(tmp_path):
    from inkterop.convert import ConvertError, convert

    _ensure_registered()
    out = tmp_path / "gated.goodnotes"
    with pytest.raises(ConvertError, match="experimental"):
        convert(FIXTURE, out)
    convert(FIXTURE, out, experimental=True)
    assert out.exists()


def test_written_file_to_pdf(tmp_path):
    from inkterop.convert import convert

    _ensure_registered()
    gn = tmp_path / "smoke.goodnotes"
    convert(FIXTURE, gn, experimental=True)
    pdf = tmp_path / "smoke.pdf"
    convert(gn, pdf)
    assert pdf.read_bytes()[:5] == b"%PDF-"
