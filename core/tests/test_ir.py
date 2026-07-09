"""IR model invariants and JSON serialization round-trip."""
from __future__ import annotations

import pytest

from inkterop import ir
from inkterop.ir import serialize


def sample_document() -> ir.Document:
    stroke = ir.Stroke(
        x=[0.0, 10.0, 20.5],
        y=[0.0, 5.0, 9.25],
        tool=ir.ToolRef(
            family=ir.ToolFamily.BALLPOINT,
            native=ir.NativeTool("remarkable", 15, {"thickness_scale": 2.0}),
        ),
        color=ir.Color(0.0, 0.0, 0.0),
        channels={
            ir.Channel.WIDTH: [2.0, 2.5, 3.0],
            ir.Channel.PRESSURE: [0.1, 0.6, 0.9],
        },
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_VARIABLE,
            color=ir.Color(0.0, 0.0, 0.0),
            cap=ir.LineCap.ROUND,
        ),
    )
    highlight = ir.Stroke(
        x=[0.0, 100.0],
        y=[50.0, 50.0],
        tool=ir.ToolRef(family=ir.ToolFamily.HIGHLIGHTER),
        color=ir.Color(1.0, 0.93, 0.46),
        channels={},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT,
            width=30.0,
            color=ir.Color(1.0, 0.93, 0.46),
            blend=ir.BlendMode.DARKEN,
            cap=ir.LineCap.SQUARE,
            opacity=0.85,
            underlay=True,
        ),
        extra={"remarkable": {"tool_raw": 18}},
    )
    page = ir.Page(
        bounds=ir.Rect(-810.0, 0.0, 810.0, 2400.0),
        point_scale=685.0 / 2160.0,
        layers=[
            ir.Layer(
                strokes=[stroke, highlight],
                texts=[ir.TextBlock(x=1.0, y=2.0, text="hello")],
                name="Layer 1",
            )
        ],
        background=ir.TemplateBackground(
            kind="dots", name="P Dots S", pitch=39.0, dot_radius=1.7
        ),
    )
    return ir.Document(
        format_id="remarkable",
        title="Test Doc",
        orientation="portrait",
        pages=[page],
        attachments={"blob": b"\x00\x01binary"},
        metadata={"uuid": "abc"},
    )


def test_validate_accepts_well_formed():
    sample_document().validate()


def test_validate_rejects_xy_mismatch():
    s = ir.Stroke(
        x=[0.0, 1.0],
        y=[0.0],
        tool=ir.ToolRef(ir.ToolFamily.PEN),
        color=ir.Color(0, 0, 0),
    )
    with pytest.raises(ValueError, match="x/y length"):
        s.validate()


def test_validate_rejects_channel_length_mismatch():
    s = ir.Stroke(
        x=[0.0, 1.0],
        y=[0.0, 1.0],
        tool=ir.ToolRef(ir.ToolFamily.PEN),
        color=ir.Color(0, 0, 0),
        channels={ir.Channel.PRESSURE: [0.5]},
    )
    with pytest.raises(ValueError, match="channel pressure"):
        s.validate()


def test_serialize_round_trip():
    doc = sample_document()
    text = serialize.dumps(doc)
    back = serialize.loads(text)
    assert serialize.dumps(back) == text  # stable fixed point
    assert back.format_id == doc.format_id
    assert back.attachments["blob"] == b"\x00\x01binary"
    page = back.pages[0]
    assert isinstance(page.background, ir.TemplateBackground)
    assert page.background.pitch == 39.0
    s0, s1 = page.layers[0].strokes
    assert s0.channels[ir.Channel.WIDTH] == [2.0, 2.5, 3.0]
    assert s0.tool.native.params == {"thickness_scale": 2.0}
    assert s1.appearance.blend is ir.BlendMode.DARKEN
    assert s1.appearance.underlay is True
    assert s1.extra == {"remarkable": {"tool_raw": 18}}
    assert page.layers[0].texts[0].text == "hello"
    back.validate()


def test_serialize_rejects_unknown_version():
    with pytest.raises(ValueError, match="unsupported IR version"):
        serialize.document_from_dict({"inkterop_ir": 999})


def test_page_strokes_skips_invisible_layers():
    doc = sample_document()
    doc.pages[0].layers[0].visible = False
    assert list(doc.pages[0].strokes()) == []
