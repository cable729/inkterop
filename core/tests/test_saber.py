"""Saber (.sba/.sbn2) reader + writer tests against a self-generated fixture."""
from __future__ import annotations

from pathlib import Path

import pytest

from inkterop import ir
from inkterop.formats.base import Fidelity
from inkterop.formats.saber import SaberReader, SaberWriter
from inkterop.formats.saber.reader import parse_bson
from inkterop.formats.saber.writer import encode_bson

FIXTURE = Path(__file__).parent / "fixtures" / "saber" / \
    "saber-mac-pens-text.sba"


def test_parse_bson_subset():
    import struct
    inner = b"\x10n\x00\x2a\x00\x00\x00"  # int32 "n" = 42
    doc = struct.pack("<i", 4 + len(inner) + 1) + inner + b"\x00"
    parsed, end = parse_bson(doc)
    assert parsed == {"n": 42}
    assert end == len(doc)


def test_detect():
    reader = SaberReader()
    assert reader.detect(FIXTURE)
    rm = Path(__file__).parent / "fixtures" / "remarkable" / "ballpoint-small.rm"
    assert not reader.detect(rm)
    gn = Path(__file__).parent / "fixtures" / "goodnotes" / \
        "gn-mac-mixed-pens.goodnotes"
    assert not reader.detect(gn)


def test_read_fixture():
    doc = SaberReader().read(FIXTURE)
    doc.validate()
    assert doc.metadata["sbn_version"] == 19
    assert len(doc.pages) == 2

    strokes = list(doc.pages[0].strokes())
    assert len(strokes) == 4
    families = sorted(s.tool.family.value for s in strokes)
    assert families == ["highlighter", "pen", "pen", "pencil"]

    hl = next(s for s in strokes if s.tool.family is ir.ToolFamily.HIGHLIGHTER)
    assert hl.appearance.underlay is True
    assert hl.appearance.opacity < 0.9  # translucent ARGB alpha
    assert ir.Channel.PRESSURE not in hl.channels  # pe=0 for highlighter

    pencil = next(s for s in strokes if s.tool.family is ir.ToolFamily.PENCIL)
    pressures = pencil.channels[ir.Channel.PRESSURE]
    assert len(pressures) == len(pencil.x)
    assert all(0.0 <= p <= 1.0 for p in pressures)
    assert max(pressures) > 0.05  # real values, not zeros

    # single-dot fountain pen stroke survives
    assert any(len(s) == 1 for s in strokes)

    # typed text (Quill delta) captured
    texts = doc.pages[0].layers[0].texts
    assert any("sadf" in t.text for t in texts)

    # geometry within page bounds
    b = doc.pages[0].bounds
    for s in strokes:
        assert all(b.x_min <= x <= b.x_max for x in s.x)
        assert all(b.y_min <= y <= b.y_max for y in s.y)


def test_fixture_to_pdf(tmp_path):
    from inkterop.convert import convert

    out = tmp_path / "saber.pdf"
    convert(FIXTURE, out)
    assert out.read_bytes()[:5] == b"%PDF-"


# ---------------------------------------------------------------- writer

def test_encode_bson_round_trip():
    doc = {
        "v": 19, "none": None, "flag": True, "off": False,
        "big": 2 ** 40, "neg": -5, "f": 1.5, "s": "héllo",
        "bin": b"\x00\x01\xff",
        "sub": {"a": 1}, "arr": [1, "two", {"three": 3.0}, b"\x07"],
    }
    parsed, end = parse_bson(encode_bson(doc))
    assert parsed == doc
    assert end == len(encode_bson(doc))


def _synthetic_doc() -> ir.Document:
    pen = ir.Stroke(
        x=[10.0, 60.0, 110.0], y=[20.0, 25.0, 20.0],
        tool=ir.ToolRef(family=ir.ToolFamily.PEN),
        color=ir.Color(0.0, 0.0, 1.0),
        channels={
            ir.Channel.WIDTH: [4.0, 4.0, 4.0],
            ir.Channel.PRESSURE: [0.2, 0.8, 0.4],
        },
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT, width=4.0,
            color=ir.Color(0.0, 0.0, 1.0), opacity=1.0,
        ),
    )
    hl = ir.Stroke(
        x=[10.0, 110.0], y=[50.0, 50.0],
        tool=ir.ToolRef(family=ir.ToolFamily.HIGHLIGHTER),
        color=ir.Color(1.0, 1.0, 0.0),
        channels={ir.Channel.WIDTH: [20.0, 20.0]},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT, width=20.0,
            color=ir.Color(1.0, 1.0, 0.0), opacity=0.5,
            underlay=True, blend=ir.BlendMode.DARKEN,
        ),
    )
    dot = ir.Stroke(
        x=[200.0], y=[200.0],
        tool=ir.ToolRef(family=ir.ToolFamily.PENCIL),
        color=ir.Color(0.0, 0.0, 0.0),
        channels={ir.Channel.WIDTH: [3.0], ir.Channel.PRESSURE: [0.6]},
    )
    pages = [
        ir.Page(bounds=ir.Rect(0.0, 0.0, 595.0, 842.0), point_scale=1.0,
                layers=[ir.Layer(strokes=[pen, hl],
                                 texts=[ir.TextBlock(x=0.0, y=0.0, text="hi")])]),
        ir.Page(bounds=ir.Rect(0.0, 0.0, 595.0, 842.0), point_scale=1.0,
                layers=[ir.Layer(strokes=[dot])]),
    ]
    return ir.Document(format_id="test", title="synthetic", pages=pages)


def test_writer_synthetic_round_trip(tmp_path):
    src = _synthetic_doc()
    out = tmp_path / "out.sbn2"
    SaberWriter().write(src, out, Fidelity.EXACT)

    back = SaberReader().read(out)
    back.validate()
    assert back.metadata["sbn_version"] == 19
    assert len(back.pages) == 2
    p0 = list(back.pages[0].strokes())
    assert len(p0) == 2
    assert sorted(s.tool.family.value for s in p0) == ["highlighter", "pen"]

    # Geometry survives the pt->saber-unit conversion and back (f32 noise
    # only). Source point_scale=1.0, saber scale 595/1000.
    k = 1.0 / (595.0 / 1000.0)
    pen = next(s for s in p0 if s.tool.family is ir.ToolFamily.PEN)
    assert pen.x[0] == pytest.approx(10.0 * k, abs=1e-3)
    assert pen.y[1] == pytest.approx(25.0 * k, abs=1e-3)
    assert pen.channels[ir.Channel.PRESSURE] == pytest.approx([0.2, 0.8, 0.4])

    hl = next(s for s in p0 if s.tool.family is ir.ToolFamily.HIGHLIGHTER)
    assert hl.appearance.underlay is True
    assert hl.appearance.opacity == pytest.approx(0.5, abs=1 / 255)
    assert ir.Channel.PRESSURE not in hl.channels

    # dot on page 2 + pressure channel survives
    p1 = list(back.pages[1].strokes())
    assert len(p1) == 1 and len(p1[0]) == 1
    assert p1[0].channels[ir.Channel.PRESSURE] == pytest.approx([0.6])

    # text round-trips (writer appends Quill-required trailing newline)
    texts = back.pages[0].layers[0].texts
    assert [t.text.rstrip("\n") for t in texts] == ["hi"]


def test_writer_fixture_round_trip(tmp_path):
    src = SaberReader().read(FIXTURE)
    out = tmp_path / "rt.sba"
    SaberWriter().write(src, out, Fidelity.EXACT)

    back = SaberReader().read(out)
    back.validate()
    assert len(back.pages) == len(src.pages)
    for sp, bp in zip(src.pages, back.pages):
        ss, bs = list(sp.strokes()), list(bp.strokes())
        assert len(bs) == len(ss)
        for a, b in zip(ss, bs):
            # NativeTool params round-trip verbatim -> identical geometry
            assert b.tool.family is a.tool.family
            assert b.tool.native.tool_id == a.tool.native.tool_id
            assert len(b) == len(a)
            assert b.x == pytest.approx(a.x, abs=1e-3)
            assert b.y == pytest.approx(a.y, abs=1e-3)
            assert b.appearance.width == pytest.approx(a.appearance.width)
            assert b.appearance.opacity == pytest.approx(
                a.appearance.opacity, abs=1 / 255)
    # fixture text survives
    assert any("sadf" in t.text
               for t in back.pages[0].layers[0].texts)


def test_writer_variable_width_stroke(tmp_path):
    """appearance.width=None (STROKED_VARIABLE, e.g. reMarkable) falls
    back to the WIDTH-channel median."""
    s = ir.Stroke(
        x=[0.0, 10.0, 20.0], y=[0.0, 0.0, 0.0],
        tool=ir.ToolRef(family=ir.ToolFamily.BALLPOINT),
        color=ir.Color(0.0, 0.0, 0.0),
        channels={ir.Channel.WIDTH: [2.0, 4.0, 6.0]},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_VARIABLE, width=None,
            color=ir.Color(0.0, 0.0, 0.0), opacity=1.0,
        ),
    )
    doc = ir.Document(format_id="test", title="vw", pages=[
        ir.Page(bounds=ir.Rect(0.0, 0.0, 100.0, 100.0), point_scale=1.0,
                layers=[ir.Layer(strokes=[s])]),
    ])
    out = tmp_path / "vw.sbn2"
    SaberWriter().write(doc, out, Fidelity.EXACT)
    back = list(SaberReader().read(out).pages[0].strokes())
    assert len(back) == 1
    k = 1.0 / (595.0 / 1000.0)
    assert back[0].channels[ir.Channel.WIDTH][0] == pytest.approx(4.0 * k)


def test_writer_experimental_gate(tmp_path):
    from inkterop.convert import ConvertError, convert

    out = tmp_path / "gated.sba"
    with pytest.raises(ConvertError, match="experimental"):
        convert(FIXTURE, out)
    convert(FIXTURE, out, experimental=True)
    assert out.exists()
