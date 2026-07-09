"""xopp reader/writer: round-trips and real-file ingestion."""
from __future__ import annotations

import gzip
import math
from pathlib import Path

import pytest

from inkterop import ir
from inkterop.formats.base import Fidelity
from inkterop.formats.xopp import XoppReader, XoppWriter

FIXTURES = Path(__file__).parent / "fixtures" / "xopp"


def make_doc() -> ir.Document:
    pen = ir.Stroke(
        x=[10.0, 20.0, 30.0],
        y=[10.0, 15.0, 12.0],
        tool=ir.ToolRef(ir.ToolFamily.BALLPOINT),
        color=ir.Color(0.0, 0.0, 1.0),
        channels={ir.Channel.WIDTH: [1.5, 2.25, 3.0],
                  ir.Channel.PRESSURE: [0.2, 0.5, 0.8]},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_VARIABLE,
            color=ir.Color(0.0, 0.0, 1.0),
        ),
    )
    marker = ir.Stroke(
        x=[0.0, 100.0],
        y=[50.0, 50.0],
        tool=ir.ToolRef(ir.ToolFamily.HIGHLIGHTER),
        color=ir.Color(1.0, 0.93, 0.46),
        channels={ir.Channel.WIDTH: [30.0, 30.0]},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT,
            width=30.0,
            color=ir.Color(1.0, 0.93, 0.46),
            opacity=0.85,
            blend=ir.BlendMode.DARKEN,
            cap=ir.LineCap.SQUARE,
            underlay=True,
        ),
    )
    page = ir.Page(
        bounds=ir.Rect(0.0, 0.0, 612.0, 792.0),
        point_scale=1.0,
        layers=[ir.Layer(strokes=[pen, marker],
                         texts=[ir.TextBlock(x=5.0, y=5.0, text="hi <&> you")])],
        background=ir.TemplateBackground(kind="dots", name="P Dots S",
                                         pitch=39.0),
    )
    return ir.Document(format_id="test", title="RT", pages=[page])


def test_round_trip(tmp_path):
    doc = make_doc()
    out = tmp_path / "rt.xopp"
    XoppWriter().write(doc, out, Fidelity.EXACT)
    back = XoppReader().read(out)
    back.validate()

    assert back.title == "RT"
    page = back.pages[0]
    assert page.bounds.width == pytest.approx(612.0)
    assert isinstance(page.background, ir.TemplateBackground)
    assert page.background.kind == "dots"

    s_pen, s_marker = page.layers[0].strokes
    orig_pen, orig_marker = doc.pages[0].layers[0].strokes

    assert s_pen.x == pytest.approx(orig_pen.x)
    assert s_pen.y == pytest.approx(orig_pen.y)
    assert s_pen.channels[ir.Channel.WIDTH] == pytest.approx(
        orig_pen.channels[ir.Channel.WIDTH]
    )
    assert s_pen.appearance.color.rgb() == pytest.approx((0.0, 0.0, 1.0), abs=0.01)

    assert s_marker.tool.family is ir.ToolFamily.HIGHLIGHTER
    assert s_marker.appearance.mode is ir.GeometryMode.STROKED_CONSTANT
    assert s_marker.appearance.width == pytest.approx(30.0)
    assert s_marker.appearance.opacity == pytest.approx(0.85, abs=0.01)
    assert s_marker.appearance.underlay is True

    text = page.layers[0].texts[0]
    assert text.text == "hi <&> you"


def test_scaled_coordinates(tmp_path):
    """A page in foreign units (rM canvas) lands in points, rebased to 0,0."""
    stroke = ir.Stroke(
        x=[-810.0, 810.0],
        y=[0.0, 2160.0],
        tool=ir.ToolRef(ir.ToolFamily.FINELINER),
        color=ir.Color(0, 0, 0),
        channels={ir.Channel.WIDTH: [2.0, 2.0]},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT, width=2.0,
            color=ir.Color(0, 0, 0),
        ),
    )
    scale = 685.0 / 2160.0
    doc = ir.Document(format_id="remarkable", pages=[
        ir.Page(bounds=ir.Rect(-810.0, 0.0, 810.0, 2160.0),
                point_scale=scale, layers=[ir.Layer(strokes=[stroke])])
    ])
    out = tmp_path / "scaled.xopp"
    XoppWriter().write(doc, out, Fidelity.EXACT)
    back = XoppReader().read(out)
    page = back.pages[0]
    assert page.bounds.width == pytest.approx(1620 * scale, abs=0.01)
    assert page.bounds.height == pytest.approx(685.0, abs=0.01)
    s = page.layers[0].strokes[0]
    assert s.x[0] == pytest.approx(0.0, abs=1e-4)
    assert s.x[1] == pytest.approx(1620 * scale, abs=0.01)
    assert s.y[1] == pytest.approx(685.0, abs=0.01)
    assert s.appearance.width == pytest.approx(2.0 * scale, abs=1e-4)


def test_reads_handwritten_xournalpp_file(tmp_path):
    """A file shaped like real Xournal++ output (single-width stroke, named
    color, no title) parses correctly."""
    xml = (
        '<?xml version="1.0" standalone="no"?>\n'
        '<xournal creator="Xournal++ 1.2.2" fileversion="4">\n'
        '<page width="595.27" height="841.89">'
        '<background type="solid" color="#ffffffff" style="graph"/>'
        "<layer>"
        '<stroke tool="pen" color="blue" width="1.41">10 10 20 20 30 15</stroke>'
        '<stroke tool="highlighter" color="#ffff007f" width="8.5">0 40 100 40</stroke>'
        "</layer>"
        "</page>\n"
        "</xournal>\n"
    )
    path = tmp_path / "real.xopp"
    with gzip.open(path, "wt") as f:
        f.write(xml)

    reader = XoppReader()
    assert reader.detect(path)
    doc = reader.read(path)
    doc.validate()
    page = doc.pages[0]
    assert isinstance(page.background, ir.TemplateBackground)
    assert page.background.kind == "grid"
    pen, hl = page.layers[0].strokes
    assert pen.appearance.mode is ir.GeometryMode.STROKED_CONSTANT
    assert pen.appearance.width == pytest.approx(1.41)
    assert len(pen.x) == 3
    assert hl.tool.family is ir.ToolFamily.HIGHLIGHTER
    assert hl.appearance.opacity == pytest.approx(0x7F / 255, abs=0.01)


def test_remarkable_fixture_to_xopp(tmp_path):
    """End-to-end: real .rm fixture -> IR -> xopp -> IR sanity."""
    from inkterop.formats.remarkable import read_page

    rm = (Path(__file__).parent / "fixtures" / "remarkable"
          / "fineliner-pencil-colors.rm")
    page = read_page(rm)
    doc = ir.Document(format_id="remarkable", title="fixture", pages=[page])
    out = tmp_path / "conv.xopp"
    XoppWriter().write(doc, out, Fidelity.EXACT)
    back = XoppReader().read(out)
    back.validate()
    orig_strokes = list(page.strokes())
    back_strokes = list(back.pages[0].strokes())
    assert len(back_strokes) == len(orig_strokes)
    # Geometry survives the unit conversion within float-text precision.
    s0, b0 = orig_strokes[0], back_strokes[0]
    scale = page.point_scale
    for i in (0, len(s0.x) // 2, len(s0.x) - 1):
        assert b0.x[i] == pytest.approx((s0.x[i] - page.bounds.x_min) * scale,
                                        abs=1e-4)
        assert b0.y[i] == pytest.approx((s0.y[i] - page.bounds.y_min) * scale,
                                        abs=1e-4)
    assert not math.isnan(sum(b0.channels[ir.Channel.WIDTH]))


def test_raw_fidelity_rejected(tmp_path):
    with pytest.raises(ValueError, match="raw pen dynamics"):
        XoppWriter().write(make_doc(), tmp_path / "x.xopp", Fidelity.RAW)


def test_single_point_stroke_becomes_valid_segment(tmp_path):
    """Xournal++ rejects strokes with < 2 points; dots must widen."""
    dot = ir.Stroke(
        x=[100.0], y=[200.0],
        tool=ir.ToolRef(ir.ToolFamily.PEN),
        color=ir.Color(0, 0, 0),
        channels={ir.Channel.WIDTH: [3.0]},
    )
    doc = ir.Document(format_id="test", pages=[
        ir.Page(bounds=ir.Rect(0, 0, 612, 792), point_scale=1.0,
                layers=[ir.Layer(strokes=[dot])])
    ])
    out = tmp_path / "dot.xopp"
    XoppWriter().write(doc, out, Fidelity.EXACT)
    back = XoppReader().read(out)
    s = back.pages[0].layers[0].strokes[0]
    assert len(s.x) >= 2  # every emitted stroke is Xournal++-legal
    assert s.x[0] == pytest.approx(100.0)
    assert s.y == pytest.approx([200.0, 200.0])
