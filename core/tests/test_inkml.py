"""InkML reader/writer: round-trips, prefix decoding, foreign files."""
from __future__ import annotations

from pathlib import Path

import pytest

from inkterop import ir
from inkterop.formats.base import Fidelity
from inkterop.formats.inkml import InkmlReader, InkmlWriter

RM_SCALE = 685.0 / 2160.0


def make_rich_doc() -> ir.Document:
    """Two layers, full channel spread, constant + variable strokes."""
    ballpoint = ir.Stroke(
        x=[-100.0, 0.0, 150.5],
        y=[10.0, 500.0, 900.25],
        tool=ir.ToolRef(ir.ToolFamily.BALLPOINT,
                        native=ir.NativeTool("remarkable", 15,
                                             {"base_width": 2.0})),
        color=ir.Color(0.0, 0.0, 1.0),
        channels={
            ir.Channel.PRESSURE: [0.2, 0.55, 0.9],
            ir.Channel.TILT_AZIMUTH: [0.1, 0.25, 0.4],
            ir.Channel.TILT_ALTITUDE: [1.5, 1.4, 1.3],
            ir.Channel.WIDTH: [4.0, 6.5, 8.0],
            ir.Channel.TIMESTAMP: [0.0, 0.012, 0.025],
        },
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_VARIABLE,
            color=ir.Color(0.0, 0.0, 1.0),
        ),
    )
    highlighter = ir.Stroke(
        x=[-300.0, 300.0],
        y=[1000.0, 1000.0],
        tool=ir.ToolRef(ir.ToolFamily.HIGHLIGHTER,
                        native=ir.NativeTool("remarkable", "5", {})),
        color=ir.Color(1.0, 0.93, 0.46),
        channels={ir.Channel.WIDTH: [30.0, 30.0]},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT,
            width=30.0,
            color=ir.Color(1.0, 0.93, 0.46, 1.0),
            opacity=0.85,
            blend=ir.BlendMode.DARKEN,
            cap=ir.LineCap.SQUARE,
            join=ir.LineCap.SQUARE,
            underlay=True,
        ),
    )
    bare = ir.Stroke(  # no channels, no appearance
        x=[0.0, 50.0, 100.0],
        y=[2000.0, 2050.0, 2000.0],
        tool=ir.ToolRef(ir.ToolFamily.FINELINER),
        color=ir.Color(1.0, 0.0, 0.0),
    )
    page = ir.Page(
        bounds=ir.Rect(-810.0, 0.0, 810.0, 2160.0),
        point_scale=RM_SCALE,
        layers=[
            ir.Layer(strokes=[ballpoint, highlighter], name="ink"),
            ir.Layer(strokes=[bare], name="notes & <extras>", visible=False),
        ],
    )
    return ir.Document(format_id="test", title="Rich <&> doc", pages=[page])


def test_round_trip(tmp_path):
    doc = make_rich_doc()
    out = tmp_path / "rt.inkml"
    InkmlWriter().write(doc, out, Fidelity.EXACT)
    back = InkmlReader().read(out)
    back.validate()

    assert back.title == "Rich <&> doc"
    assert back.orientation == "portrait"

    page, orig_page = back.pages[0], doc.pages[0]
    # Page metadata round-trips exactly (annotationXML carries full floats).
    assert page.bounds.x_min == orig_page.bounds.x_min
    assert page.bounds.y_min == orig_page.bounds.y_min
    assert page.bounds.x_max == orig_page.bounds.x_max
    assert page.bounds.y_max == orig_page.bounds.y_max
    assert page.point_scale == orig_page.point_scale

    assert len(page.layers) == 2
    assert page.layers[0].name == "ink"
    assert page.layers[0].visible is True
    assert page.layers[1].name == "notes & <extras>"
    assert page.layers[1].visible is False

    # Geometry within 1e-3 pt (source-unit tolerance = 1e-3 / point_scale).
    tol = 1e-3 / RM_SCALE
    for layer, orig_layer in zip(page.layers, orig_page.layers):
        assert len(layer.strokes) == len(orig_layer.strokes)
        for s, o in zip(layer.strokes, orig_layer.strokes):
            assert s.x == pytest.approx(o.x, abs=tol)
            assert s.y == pytest.approx(o.y, abs=tol)
            assert set(s.channels) == set(o.channels)
            for ch, vals in o.channels.items():
                chtol = tol if ch is ir.Channel.WIDTH else 1e-4
                assert s.channels[ch] == pytest.approx(vals, abs=chtol)
            assert s.tool.family is o.tool.family

    bp, hl = page.layers[0].strokes
    # Native tool carries through, including tool_id type and params.
    assert bp.tool.native.format_id == "remarkable"
    assert bp.tool.native.tool_id == 15
    assert bp.tool.native.params == {"base_width": 2.0}
    assert hl.tool.native.tool_id == "5"
    # Semantic color and appearance fields round-trip exactly.
    assert bp.color == ir.Color(0.0, 0.0, 1.0)
    assert bp.appearance.mode is ir.GeometryMode.STROKED_VARIABLE
    assert bp.appearance.width is None
    assert hl.appearance.mode is ir.GeometryMode.STROKED_CONSTANT
    assert hl.appearance.width == 30.0
    assert hl.appearance.opacity == 0.85
    assert hl.appearance.blend is ir.BlendMode.DARKEN
    assert hl.appearance.cap is ir.LineCap.SQUARE
    assert hl.appearance.join is ir.LineCap.SQUARE
    assert hl.appearance.underlay is True
    assert hl.appearance.color == ir.Color(1.0, 0.93, 0.46, 1.0)

    bare = page.layers[1].strokes[0]
    assert bare.appearance is None
    assert bare.channels == {}
    assert bare.color == ir.Color(1.0, 0.0, 0.0)


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(
        '<ink xmlns="http://www.w3.org/2003/InkML">' + body + "</ink>",
        encoding="utf-8",
    )
    return path


def test_single_quote_prefix_decoding(tmp_path):
    path = _write(tmp_path, "vel.inkml",
                  "<trace>10 20, '5 '5, '5 '5</trace>")
    s = InkmlReader().read(path).pages[0].layers[0].strokes[0]
    assert s.x == pytest.approx([10.0, 15.0, 20.0])
    assert s.y == pytest.approx([20.0, 25.0, 30.0])


def test_single_quote_mode_persists(tmp_path):
    """Per spec, unprefixed values keep the last established difference
    order for that channel."""
    path = _write(tmp_path, "vel2.inkml",
                  "<trace>10 20, '5 '5, 5 5, !40 !40</trace>")
    s = InkmlReader().read(path).pages[0].layers[0].strokes[0]
    assert s.x == pytest.approx([10.0, 15.0, 20.0, 40.0])
    assert s.y == pytest.approx([20.0, 25.0, 30.0, 40.0])


def test_double_quote_prefix_decoding(tmp_path):
    path = _write(tmp_path, "acc.inkml",
                  '<trace>10 20, \'5 \'10, "2 "-3, "2 "-3</trace>')
    s = InkmlReader().read(path).pages[0].layers[0].strokes[0]
    # velocities: (5,10) -> (7,7) -> (9,4)
    assert s.x == pytest.approx([10.0, 15.0, 22.0, 31.0])
    assert s.y == pytest.approx([20.0, 30.0, 37.0, 41.0])


def test_foreign_inkml(tmp_path):
    """Minimal InkML with no inkterop annotations parses cleanly."""
    path = _write(
        tmp_path, "foreign.inkml",
        "<traceFormat>"
        '<channel name="X" type="decimal"/><channel name="Y" type="decimal"/>'
        "</traceFormat>"
        "<trace> 10 10, 20 20, 30 15</trace>"
        "<trace>0 40, 100 40</trace>",
    )
    reader = InkmlReader()
    assert reader.detect(path)
    doc = reader.read(path)
    doc.validate()
    page = doc.pages[0]
    strokes = list(page.strokes())
    assert len(strokes) == 2
    assert strokes[0].x == pytest.approx([10.0, 20.0, 30.0])
    assert strokes[0].tool.family is ir.ToolFamily.PEN
    assert strokes[0].appearance is None
    assert page.point_scale == 1.0
    # Bounds computed from trace extents.
    assert page.bounds.x_min == 0.0
    assert page.bounds.x_max == 100.0
    assert page.bounds.y_max == 40.0


def test_foreign_brush_and_context(tmp_path):
    """A foreign context reorders channels; a foreign brush gives color."""
    path = _write(
        tmp_path, "foreign2.inkml",
        "<definitions>"
        '<context xml:id="c"><traceFormat>'
        '<channel name="F" type="decimal"/>'
        '<channel name="X" type="decimal"/><channel name="Y" type="decimal"/>'
        "</traceFormat></context>"
        '<brush xml:id="b"><brushProperty name="color" value="#ff0000"/></brush>'
        "</definitions>"
        '<trace contextRef="#c" brushRef="#b">0.5 10 20, 0.75 30 40</trace>',
    )
    doc = InkmlReader().read(path)
    s = doc.pages[0].layers[0].strokes[0]
    assert s.x == pytest.approx([10.0, 30.0])
    assert s.y == pytest.approx([20.0, 40.0])
    assert s.channels[ir.Channel.PRESSURE] == pytest.approx([0.5, 0.75])
    assert s.color.rgb() == pytest.approx((1.0, 0.0, 0.0), abs=0.01)


def test_remarkable_fixture_round_trip(tmp_path):
    """End-to-end: real .rm fixture -> IR -> InkML -> IR."""
    from inkterop.formats.remarkable import read_page

    rm = (Path(__file__).parent / "fixtures" / "remarkable"
          / "fineliner-pencil-colors.rm")
    page = read_page(rm)
    doc = ir.Document(format_id="remarkable", title="fixture", pages=[page])
    out = tmp_path / "conv.inkml"
    InkmlWriter().write(doc, out, Fidelity.RAW)
    assert InkmlReader().detect(out)
    back = InkmlReader().read(out)
    back.validate()

    orig_strokes = list(page.strokes())
    back_strokes = list(back.pages[0].strokes())
    assert len(back_strokes) == len(orig_strokes)
    assert back.pages[0].point_scale == page.point_scale

    assert any(ir.Channel.PRESSURE in s.channels for s in orig_strokes)
    tol = 1e-3 / page.point_scale
    for o, b in zip(orig_strokes, back_strokes):
        assert len(b.x) == len(o.x)
        assert b.x == pytest.approx(o.x, abs=tol)
        assert b.y == pytest.approx(o.y, abs=tol)
        assert b.tool.family is o.tool.family
        if ir.Channel.PRESSURE in o.channels:
            assert b.channels[ir.Channel.PRESSURE] == pytest.approx(
                o.channels[ir.Channel.PRESSURE], abs=1e-4)


def test_raw_and_exact_identical(tmp_path):
    doc = make_rich_doc()
    exact, raw = tmp_path / "e.inkml", tmp_path / "r.inkml"
    InkmlWriter().write(doc, exact, Fidelity.EXACT)
    InkmlWriter().write(doc, raw, Fidelity.RAW)
    assert exact.read_bytes() == raw.read_bytes()


def test_native_fidelity_restyles(tmp_path):
    """NATIVE rebuilds brushes from tool-family defaults: the highlighter
    keeps DARKEN+underlay, the bare fineliner gains a constant width."""
    doc = make_rich_doc()
    out = tmp_path / "native.inkml"
    InkmlWriter().write(doc, out, Fidelity.NATIVE)
    back = InkmlReader().read(out)
    hl = back.pages[0].layers[0].strokes[1]
    assert hl.appearance.blend is ir.BlendMode.DARKEN
    assert hl.appearance.underlay is True
    bare = back.pages[0].layers[1].strokes[0]
    assert bare.appearance is not None
    assert bare.appearance.mode is ir.GeometryMode.STROKED_CONSTANT


def test_detect_rejects_non_inkml(tmp_path):
    p = tmp_path / "x.inkml"
    p.write_text('<?xml version="1.0"?><svg>ink</svg>')
    assert not InkmlReader().detect(p)
    assert not InkmlReader().detect(tmp_path / "missing.inkml")
