"""Notability .ntb writer tests: FbBuilder framing (validated against both
the ntb.py reader's _Table accessors and the schema-less fbwalk explorer),
point-blob inversion, synthetic and fixture round-trips, fidelity/gating.
"""
from __future__ import annotations

import importlib.util
import math
import struct
import sys
import zipfile
from pathlib import Path

import pytest

from inkterop import ir
from inkterop.formats.base import Fidelity
from inkterop.formats.notability.fb import FbBuilder
from inkterop.formats.notability.ntb import (
    NtbReader,
    _Table,
    decode_point_blob,
)
from inkterop.formats.notability.writer import (
    NtbWriter,
    document_to_note_bundle,
    encode_point_blob,
)

FIXTURE = Path(__file__).parent / "fixtures" / "notability" / "scribbles.ntb"

# tools/re/fbwalk.py is a standalone RE script, not a package member.
_spec = importlib.util.spec_from_file_location(
    "fbwalk", Path(__file__).parents[2] / "tools" / "re" / "fbwalk.py")
fbwalk = importlib.util.module_from_spec(_spec)
sys.modules["fbwalk"] = fbwalk  # @dataclass resolves types via sys.modules
_spec.loader.exec_module(fbwalk)


def _assert_no_errors(node, path="root"):
    assert node.kind != "error", f"fbwalk rejected {path}: {node.label}"
    for name, child in node.children:
        _assert_no_errors(child, f"{path}.{name}")


# ------------------------------------------------------------- FbBuilder

def test_builder_all_field_kinds():
    fb = FbBuilder()
    inner = fb.table({0: ("ref", fb.string("hello"))})
    vec = fb.vector_of_tables([inner, fb.table({0: ("u8", 7)})])
    buf = fb.finish(fb.table({
        0: ("u8", 5),
        1: ("u16", 12),
        2: ("u32", 0xDEADBEEF),
        3: ("u64", 1_783_612_550_506),
        4: ("f32", 3.1875),
        5: ("f32s", (612.0, 792.0)),
        6: ("struct", bytes([1, 2, 3, 4]), 4),
        7: ("ref", fb.string("Inter")),
        8: ("ref", fb.byte_vector(b"\x00\x01\xfe\xff")),
        9: ("ref", vec),
        10: ("ref", inner),
        # slot 11 intentionally absent
        12: ("u8", 0),  # explicit zero must still be present
    }))

    # read back through the reader's own accessors
    t = _Table(buf, struct.unpack_from("<I", buf, 0)[0])
    assert t.u8(0) == 5
    assert struct.unpack_from("<H", buf, t._field(1))[0] == 12
    assert struct.unpack_from("<I", buf, t._field(2))[0] == 0xDEADBEEF
    assert t.u64(3) == 1_783_612_550_506
    assert t.f32s(4, 1)[0] == pytest.approx(3.1875)
    assert t.f32s(5, 2) == (612.0, 792.0)
    assert t.bytes_at(6, 4) == bytes([1, 2, 3, 4])
    assert t.string(7) == "Inter"
    assert t.byte_vector(8) == b"\x00\x01\xfe\xff"
    tables = t.vector(9)
    assert len(tables) == 2
    assert _Table(buf, tables[0]).string(0) == "hello"
    assert _Table(buf, tables[1]).u8(0) == 7
    assert t.table(10).string(0) == "hello"
    assert t._field(11) is None  # absent slot
    assert t.u8(12) == 0  # present slot with zero value

    # FlatBuffers alignment: scalars self-aligned, offsets 4-aligned
    assert t._field(3) % 8 == 0
    assert t._field(2) % 4 == 0
    assert t._field(1) % 2 == 0
    assert t._field(9) % 4 == 0
    assert len(buf) % 8 == 0

    # the independent schema-less walker accepts every object
    root = fbwalk.walk_file(buf)
    _assert_no_errors(root)
    kinds = {name: n.kind for name, n in root.children}
    assert kinds["field_7"] == "string"
    assert kinds["field_9"] == "vector"
    assert kinds["field_10"] == "table"


def test_builder_empty_table_and_string_padding():
    fb = FbBuilder()
    empty = fb.table({})
    buf = fb.finish(fb.table({0: ("ref", empty), 1: ("ref", fb.string("ab"))}))
    t = _Table(buf, struct.unpack_from("<I", buf, 0)[0])
    assert t.table(0)._slots == ()
    assert t.string(1) == "ab"
    _assert_no_errors(fbwalk.walk_file(buf))


# ------------------------------------------------------------ point blob

def test_point_blob_inverts_reader():
    xs = [0.0, 10.0, 10.0, -5.5]
    ys = [0.0, 0.0, 20.0, 20.25]
    mults = [1.25, 1.0, 0.5, 0.375]  # f16-exact values
    blob = encode_point_blob(xs, ys, mults)
    # exact [verified] framing: fmt-1 header 12B, 31B records, 6B tail
    assert blob[0] == 1
    assert struct.unpack_from("<H", blob, 1)[0] == 4
    assert blob[3] == 3
    assert blob[4:12] == bytes(8)
    assert len(blob) == 12 + 3 * (7 + 24) + 6

    segments, widths = decode_point_blob(blob)  # strict parser must accept
    assert widths == pytest.approx(mults)
    assert [tuple(s[2]) for s in segments] == [(10.0, 0.0), (10.0, 20.0),
                                               (-5.5, 20.25)]
    # controls sit at exact thirds -> the cubic is exactly linear
    c1, c2, end = segments[0]
    assert c1 == pytest.approx((10.0 / 3.0, 0.0))
    assert c2 == pytest.approx((20.0 / 3.0, 0.0))


def test_point_blob_single_anchor():
    blob = encode_point_blob([0.0], [0.0], [1.0])
    assert len(blob) == 18
    segments, widths = decode_point_blob(blob)
    assert segments == [] and widths == [1.0]


# --------------------------------------------------------- IR round-trip

def _max_deviation(bx, by, poly_x, poly_y) -> float:
    """Max distance from read-back points to the written polyline."""
    def seg_dist(px, py, ax, ay, cx, cy):
        dx, dy = cx - ax, cy - ay
        l2 = dx * dx + dy * dy
        if l2 == 0.0:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
        return math.hypot(px - ax - t * dx, py - ay - t * dy)

    n = len(poly_x)
    if n == 1:
        return max(math.hypot(px - poly_x[0], py - poly_y[0])
                   for px, py in zip(bx, by))
    return max(min(seg_dist(px, py, poly_x[i], poly_y[i],
                            poly_x[i + 1], poly_y[i + 1])
                   for i in range(n - 1))
               for px, py in zip(bx, by))


def _synthetic_doc() -> ir.Document:
    pen = ir.Stroke(  # variable width, no appearance.width
        x=[30.0, 80.0, 130.0, 90.0], y=[40.0, 45.5, 40.0, 100.0],
        tool=ir.ToolRef(family=ir.ToolFamily.PEN),
        color=ir.Color(0.0, 0.0, 1.0),
        channels={ir.Channel.WIDTH: [2.0, 4.0, 6.0, 4.0]},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_VARIABLE, width=None,
            color=ir.Color(0.0, 0.0, 1.0), opacity=1.0,
        ),
    )
    pencil = ir.Stroke(
        x=[200.0, 260.0], y=[300.0, 310.0],
        tool=ir.ToolRef(family=ir.ToolFamily.PENCIL),
        color=ir.Color(1.0, 0.0, 0.0),
        channels={ir.Channel.WIDTH: [3.0, 3.0]},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT, width=3.0,
            color=ir.Color(1.0, 0.0, 0.0), opacity=1.0,
        ),
    )
    hl = ir.Stroke(
        x=[50.0, 150.0, 250.0], y=[500.0, 520.0, 500.0],
        tool=ir.ToolRef(family=ir.ToolFamily.HIGHLIGHTER),
        color=ir.Color(1.0, 1.0, 0.0),
        channels={ir.Channel.WIDTH: [16.0, 16.0, 16.0]},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT, width=16.0,
            color=ir.Color(1.0, 1.0, 0.0), opacity=0.42, underlay=True,
        ),
    )
    dot = ir.Stroke(  # single-point stroke must survive
        x=[400.0], y=[600.0],
        tool=ir.ToolRef(family=ir.ToolFamily.PEN),
        color=ir.Color(0.0, 0.0, 0.0),
        channels={ir.Channel.WIDTH: [3.1875]},
    )
    return ir.Document(format_id="test", title="synthetic ntb", pages=[
        ir.Page(bounds=ir.Rect(0.0, 0.0, 612.0, 792.0), point_scale=1.0,
                layers=[ir.Layer(strokes=[pen, pencil, hl, dot])]),
    ])


def test_synthetic_round_trip(tmp_path):
    src = _synthetic_doc()
    out = tmp_path / "synthetic.ntb"
    NtbWriter().write(src, out, Fidelity.EXACT)

    assert NtbReader().detect(out)
    back = NtbReader().read(out)
    back.validate()
    assert back.title == "synthetic ntb"
    assert back.metadata["app_version"] == "16.5.3"
    assert back.pages[0].bounds.width == pytest.approx(612.0)

    src_strokes = list(src.pages[0].strokes())
    bk_strokes = list(back.pages[0].strokes())
    assert len(bk_strokes) == len(src_strokes)
    for a, b in zip(src_strokes, bk_strokes):
        assert b.tool.family is a.tool.family
        # colors are exact bytes both ways
        assert (b.color.r, b.color.g, b.color.b) == (
            a.color.r, a.color.g, a.color.b)
        # reader flattening resamples: every read-back point must lie on
        # the written polyline (linear cubics), anchors round-trip in f32
        assert _max_deviation(b.x, b.y, a.x, a.y) <= 0.1
        assert b.x[0] == pytest.approx(a.x[0], abs=1e-4)
        assert b.y[-1] == pytest.approx(a.y[-1], abs=1e-4)

    pen_b, pencil_b, hl_b, dot_b = bk_strokes
    # base widths (payload field_8): median for variable, exact for constant
    assert pen_b.tool.native.params["width"] == pytest.approx(4.0)
    assert pencil_b.tool.native.params["width"] == pytest.approx(3.0)
    assert hl_b.tool.native.params["width"] == pytest.approx(16.0)
    # width profile survives via f16 multipliers (2/4/6 are f16-exact)
    pen_ws = pen_b.channels[ir.Channel.WIDTH]
    assert pen_ws[0] == pytest.approx(2.0)
    assert max(pen_ws) == pytest.approx(6.0)
    # tool ids + highlighter opacity/underlay
    assert pen_b.tool.native.tool_id == 0
    assert pencil_b.tool.native.tool_id == 1
    assert hl_b.tool.native.tool_id == 2
    assert hl_b.appearance.opacity == pytest.approx(0.42, abs=1 / 255)
    assert hl_b.appearance.underlay is True
    assert len(dot_b) == 1


def test_native_fidelity_uses_family_defaults(tmp_path):
    out = tmp_path / "native.ntb"
    NtbWriter().write(_synthetic_doc(), out, Fidelity.NATIVE)
    back = list(NtbReader().read(out).pages[0].strokes())
    pen, pencil, hl = back[0], back[1], back[2]
    assert pen.tool.native.params["width"] == pytest.approx(3.1875)
    assert pencil.tool.native.params["width"] == pytest.approx(3.1875)
    assert hl.tool.native.params["width"] == pytest.approx(15.9375)
    ws = pen.channels[ir.Channel.WIDTH]  # multipliers forced to 1.0
    assert min(ws) == max(ws) == pytest.approx(3.1875)
    assert hl.appearance.opacity == pytest.approx(107 / 255, abs=1 / 255)


def test_raw_fidelity_raises():
    with pytest.raises(ValueError, match="raw pen dynamics"):
        document_to_note_bundle(_synthetic_doc(), Fidelity.RAW)


def test_multi_page_writes_first_and_warns(tmp_path, caplog):
    doc = _synthetic_doc()
    doc.pages.append(ir.Page(
        bounds=ir.Rect(0.0, 0.0, 612.0, 792.0), point_scale=1.0,
        layers=[ir.Layer(strokes=[ir.Stroke(
            x=[1.0, 2.0], y=[1.0, 2.0],
            tool=ir.ToolRef(family=ir.ToolFamily.PEN),
            color=ir.Color(0, 0, 0),
            channels={ir.Channel.WIDTH: [2.0, 2.0]})])],
    ))
    out = tmp_path / "multi.ntb"
    with caplog.at_level("WARNING"):
        NtbWriter().write(doc, out, Fidelity.EXACT)
    assert any("single-page" in r.message for r in caplog.records)
    back = NtbReader().read(out)
    assert len(back.pages) == 1
    assert len(list(back.pages[0].strokes())) == 4  # page-2 stroke dropped


# ------------------------------------------------------ fixture round-trip

def test_fixture_round_trip(tmp_path):
    src = NtbReader().read(FIXTURE)
    out = tmp_path / "rt.ntb"
    NtbWriter().write(src, out, Fidelity.EXACT)

    back = NtbReader().read(out)
    back.validate()
    src_strokes = list(src.pages[0].strokes())
    bk_strokes = list(back.pages[0].strokes())
    assert len(src_strokes) == 4 and len(bk_strokes) == 4

    for a, b in zip(src_strokes, bk_strokes):
        assert b.tool.family is a.tool.family
        assert b.tool.native.tool_id == a.tool.native.tool_id
        # colors byte-exact, base width from the NativeTool round-trip
        assert (b.color.r, b.color.g, b.color.b) == (
            a.color.r, a.color.g, a.color.b)
        assert b.appearance.opacity == pytest.approx(
            a.appearance.opacity, abs=1 / 255)
        assert b.tool.native.params["width"] == pytest.approx(
            a.tool.native.params["width"])
        # per-stroke extents within 0.5 pt
        assert min(b.x) == pytest.approx(min(a.x), abs=0.5)
        assert max(b.x) == pytest.approx(max(a.x), abs=0.5)
        assert min(b.y) == pytest.approx(min(a.y), abs=0.5)
        assert max(b.y) == pytest.approx(max(a.y), abs=0.5)
        assert _max_deviation(b.x, b.y, a.x, a.y) <= 0.1

    assert back.title == src.title
    assert back.metadata["created_unix_ms"] == src.metadata["created_unix_ms"]
    assert back.metadata["notability_uuid"] == src.metadata["notability_uuid"]


def test_written_container_mirrors_fixture(tmp_path):
    out = tmp_path / "container.ntb"
    NtbWriter().write(_synthetic_doc(), out, Fidelity.EXACT)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert names == ["version", "noteBundle", "manifest.json",
                         "thumbnail.png"]
        assert all(i.compress_type == zipfile.ZIP_STORED
                   for i in zf.infolist())
        assert zf.read("version") == b"1"
        assert zf.read("manifest.json") == b'{\n  "appVersion" : "16.5.3"\n}'
        assert zf.read("thumbnail.png")[:8] == b"\x89PNG\r\n\x1a\n"
        bundle = zf.read("noteBundle")

    # the schema-less walker must accept the whole written FlatBuffer
    root = fbwalk.walk_file(bundle, max_depth=16, max_vec=64)
    _assert_no_errors(root)
    fields = dict(root.children)
    assert fields["field_6"].label.startswith("vector[6] of tables")
    assert fields["field_3"].kind == "string"  # uppercase UUID
    assert fields["field_5"].kind == "string"  # lowercase UUID

    # root constants mirror the fixture: u16 = 12, opaque 16B, created u64
    t = _Table(bundle, struct.unpack_from("<I", bundle, 0)[0])
    assert struct.unpack_from("<H", bundle, t._field(7))[0] == 12
    assert t.bytes_at(0, 16) == bytes(16)
    assert t._field(4) % 8 == 0  # u64 stays absolutely aligned
    # op sequence numbers: 0, 1, then odd ascending for strokes
    seqs = [struct.unpack_from("<II", bundle, _Table(bundle, p)._field(0))[1]
            for p in t.vector(6)]
    assert seqs == [0, 1, 3, 5, 7, 9]
    types = [_Table(bundle, p).u8(4) for p in t.vector(6)]
    assert types == [1, 3, 15, 15, 15, 15]


# --------------------------------------------------------------- gating

def test_experimental_gate(tmp_path):
    from inkterop import formats
    from inkterop.convert import ConvertError, convert

    # HARD RULE: the registry module is not edited in this change; use the
    # public registration hook so convert() can find the writer.
    if not any(w.format_id == "notability" for w in formats.writers()):
        formats.register_writer(NtbWriter())

    out = tmp_path / "gated.ntb"
    with pytest.raises(ConvertError, match="experimental"):
        convert(FIXTURE, out)
    convert(FIXTURE, out, experimental=True)
    assert out.exists()
    assert len(list(NtbReader().read(out).pages[0].strokes())) == 4
