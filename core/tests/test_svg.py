"""SVG writer: styling, geometry, outline tessellation, end-to-end."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from inkterop import ir
from inkterop.formats.base import Fidelity
from inkterop.render.svg import SvgWriter, outline_polygon

NS = {"svg": "http://www.w3.org/2000/svg"}
FIXTURES = Path(__file__).parent / "fixtures" / "remarkable"


def make_doc() -> ir.Document:
    fineliner = ir.Stroke(
        x=[10.0, 20.0, 30.0],
        y=[10.0, 15.0, 12.0],
        tool=ir.ToolRef(ir.ToolFamily.FINELINER),
        color=ir.Color(0.0, 0.0, 1.0),
        channels={ir.Channel.WIDTH: [2.0, 2.0, 2.0],
                  ir.Channel.PRESSURE: [0.2, 0.5, 0.8]},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT, width=2.0,
            color=ir.Color(0.0, 0.0, 1.0),
        ),
    )
    ballpoint = ir.Stroke(
        x=[40.0, 50.0, 60.0],
        y=[40.0, 45.0, 42.0],
        tool=ir.ToolRef(ir.ToolFamily.BALLPOINT),
        color=ir.Color(0.0, 0.0, 0.0),
        channels={ir.Channel.WIDTH: [1.5, 2.25, 3.0]},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_VARIABLE,
            color=ir.Color(0.0, 0.0, 0.0),
        ),
    )
    marker = ir.Stroke(
        x=[0.0, 100.0],
        y=[50.0, 50.0],
        tool=ir.ToolRef(ir.ToolFamily.HIGHLIGHTER),
        color=ir.Color(1.0, 0.93, 0.46),
        channels={ir.Channel.WIDTH: [30.0, 30.0]},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT, width=30.0,
            color=ir.Color(1.0, 0.93, 0.46), opacity=0.85,
            blend=ir.BlendMode.DARKEN, cap=ir.LineCap.SQUARE, underlay=True,
        ),
    )
    dot = ir.Stroke(
        x=[70.0],
        y=[70.0],
        tool=ir.ToolRef(ir.ToolFamily.FINELINER),
        color=ir.Color(0.0, 0.0, 0.0),
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT, width=4.0,
            color=ir.Color(0.0, 0.0, 0.0),
        ),
    )
    page = ir.Page(
        bounds=ir.Rect(0.0, 0.0, 200.0, 200.0),
        point_scale=1.0,
        layers=[ir.Layer(strokes=[fineliner, ballpoint, marker, dot],
                         texts=[ir.TextBlock(x=5.0, y=5.0, text="hi <&> you")],
                         name="L1")],
        background=ir.TemplateBackground(kind="dots", name="P Dots S",
                                         pitch=39.0, dot_radius=1.0),
    )
    return ir.Document(format_id="test", title="svg", pages=[page])


def render(tmp_path, doc=None, fidelity=Fidelity.EXACT, options=None):
    out = tmp_path / "out.svg"
    SvgWriter().write(doc or make_doc(), out, fidelity, options)
    return ET.parse(out).getroot()


def d_points(d: str) -> list[tuple[float, float]]:
    nums = [float(t) for t in re.findall(r"-?\d+(?:\.\d+)?", d)]
    return list(zip(nums[::2], nums[1::2]))


def test_constant_width_stroke(tmp_path):
    root = render(tmp_path)
    stroked = [p for p in root.findall(".//svg:path", NS)
               if p.get("stroke") == "#0000ff"]
    assert len(stroked) == 1
    p = stroked[0]
    assert p.get("fill") == "none"
    assert float(p.get("stroke-width")) == pytest.approx(2.0)
    assert p.get("stroke-linecap") == "round"
    assert p.get("stroke-linejoin") == "round"
    assert float(p.get("stroke-opacity")) == pytest.approx(1.0)


def test_variable_width_stroke_is_filled_outline(tmp_path):
    root = render(tmp_path)
    filled = [p for p in root.findall(".//svg:path", NS)
              if p.get("fill") == "#000000"]
    assert len(filled) == 1
    p = filled[0]
    assert p.get("stroke") is None
    assert p.get("d").rstrip().endswith("Z")
    assert float(p.get("fill-opacity")) == pytest.approx(1.0)


def test_highlighter_blend_and_underlay_order(tmp_path):
    root = render(tmp_path)
    groups = root.findall("svg:g[@data-rmi-layer]", NS)
    assert len(groups) == 2  # underlay pass, then ink pass
    marker = groups[0].findall("svg:path", NS)[0]
    assert "mix-blend-mode:darken" in marker.get("style", "")
    assert marker.get("stroke-linecap") == "square"
    assert float(marker.get("stroke-opacity")) == pytest.approx(0.85)


def test_dot_stroke_becomes_circle(tmp_path):
    root = render(tmp_path)
    dots = [c for c in root.findall(".//svg:g[@data-rmi-layer]/svg:circle", NS)]
    assert len(dots) == 1
    c = dots[0]
    assert float(c.get("r")) == pytest.approx(2.0)  # width 4 / 2
    assert (float(c.get("cx")), float(c.get("cy"))) == (70.0, 70.0)


def test_geometry_transform(tmp_path):
    scale = 685.0 / 2160.0
    stroke = ir.Stroke(
        x=[-810.0, 810.0], y=[0.0, 2160.0],
        tool=ir.ToolRef(ir.ToolFamily.FINELINER),
        color=ir.Color(0, 0, 0),
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT, width=2.0,
            color=ir.Color(0, 0, 0),
        ),
    )
    doc = ir.Document(format_id="remarkable", pages=[
        ir.Page(bounds=ir.Rect(-810.0, 0.0, 810.0, 2160.0),
                point_scale=scale, layers=[ir.Layer(strokes=[stroke])])
    ])
    root = render(tmp_path, doc)
    assert root.get("viewBox").split()[:2] == ["0", "0"]
    w, h = (float(v) for v in root.get("viewBox").split()[2:])
    assert w == pytest.approx(1620 * scale, abs=0.01)
    assert h == pytest.approx(685.0, abs=0.01)
    p = root.findall(".//svg:path", NS)[0]
    (x0, y0), (x1, y1) = d_points(p.get("d"))
    assert x0 == pytest.approx(0.0, abs=0.01)
    assert y0 == pytest.approx(0.0, abs=0.01)
    assert x1 == pytest.approx(1620 * scale, abs=0.01)
    assert y1 == pytest.approx(685.0, abs=0.01)
    assert float(p.get("stroke-width")) == pytest.approx(2.0 * scale, abs=0.01)


def test_outline_tessellator_offsets():
    """Horizontal stroke, widths [2, 4, 2]: outline hits y +- width/2."""
    pts = outline_polygon([0.0, 10.0, 20.0], [10.0, 10.0, 10.0],
                          [1.0, 2.0, 1.0], round_caps=False)
    expected = [(0.0, 11.0), (10.0, 12.0), (20.0, 11.0),
                (20.0, 9.0), (10.0, 8.0), (0.0, 9.0)]
    assert len(pts) == len(expected)
    for (x, y), (ex, ey) in zip(pts, expected):
        assert x == pytest.approx(ex, abs=1e-9)
        assert y == pytest.approx(ey, abs=1e-9)


def test_outline_tessellator_in_output(tmp_path):
    stroke = ir.Stroke(
        x=[0.0, 10.0, 20.0], y=[10.0, 10.0, 10.0],
        tool=ir.ToolRef(ir.ToolFamily.BALLPOINT),
        color=ir.Color(0, 0, 0),
        channels={ir.Channel.WIDTH: [2.0, 4.0, 2.0]},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_VARIABLE, color=ir.Color(0, 0, 0),
            cap=ir.LineCap.BUTT,
        ),
    )
    doc = ir.Document(format_id="test", pages=[
        ir.Page(bounds=ir.Rect(0, 0, 50, 50), point_scale=1.0,
                layers=[ir.Layer(strokes=[stroke])])
    ])
    root = render(tmp_path, doc)
    p = root.findall(".//svg:path", NS)[0]
    got = d_points(p.get("d"))
    expected = [(0, 11), (10, 12), (20, 11), (20, 9), (10, 8), (0, 9)]
    assert len(got) == 6
    for (x, y), (ex, ey) in zip(got, expected):
        assert x == pytest.approx(ex, abs=0.01)
        assert y == pytest.approx(ey, abs=0.01)


def test_round_caps_add_fan_points(tmp_path):
    pts = outline_polygon([0.0, 20.0], [10.0, 10.0], [2.0, 2.0],
                          round_caps=True)
    assert len(pts) == 4 + 2 * 7  # sides + two 8-segment fans
    # End-cap fan apex extends past x=20 by the half-width.
    assert max(x for x, _ in pts) == pytest.approx(22.0, abs=1e-9)
    assert min(x for x, _ in pts) == pytest.approx(-2.0, abs=1e-9)


def test_degenerate_variable_stroke_is_circle(tmp_path):
    stroke = ir.Stroke(
        x=[5.0, 5.0, 5.0], y=[5.0, 5.0, 5.0],
        tool=ir.ToolRef(ir.ToolFamily.BALLPOINT),
        color=ir.Color(0, 0, 0),
        channels={ir.Channel.WIDTH: [2.0, 3.0, 2.0]},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_VARIABLE, color=ir.Color(0, 0, 0),
        ),
    )
    doc = ir.Document(format_id="test", pages=[
        ir.Page(bounds=ir.Rect(0, 0, 50, 50), point_scale=1.0,
                layers=[ir.Layer(strokes=[stroke])])
    ])
    root = render(tmp_path, doc)
    c = root.findall(".//svg:g[@data-rmi-layer]/svg:circle", NS)[0]
    assert float(c.get("r")) == pytest.approx(1.5)  # max halfwidth


def test_multipage_writes_suffixed_files(tmp_path):
    doc = make_doc()
    doc.pages.append(doc.pages[0])
    out = tmp_path / "multi.svg"
    SvgWriter().write(doc, out, Fidelity.EXACT)
    assert out.exists()
    assert (tmp_path / "multi-p2.svg").exists()
    ET.parse(tmp_path / "multi-p2.svg")


def test_embed_raw_attrs(tmp_path):
    root = render(tmp_path)
    stroked = [p for p in root.findall(".//svg:path", NS)
               if p.get("stroke") == "#0000ff"][0]
    assert stroked.get("data-rmi-tool") == "fineliner"
    assert stroked.get("data-rmi-pressure") == "0.2 0.5 0.8"
    assert stroked.get("data-rmi-width") == "2.0 2.0 2.0"

    root = render(tmp_path, options={"embed_raw": False})
    for el in root.iter():
        for attr in el.attrib:
            assert not attr.startswith("data-rmi-tool")
            assert not attr.startswith("data-rmi-pressure")
            assert not attr.startswith("data-rmi-width")


def test_template_and_text(tmp_path):
    root = render(tmp_path)
    dots = root.findall("svg:g[@data-rmi-template='dots']/svg:circle", NS)
    assert len(dots) > 0
    text = root.findall(".//svg:text", NS)[0]
    assert text.text == "hi <&> you"
    assert float(text.get("x")) == pytest.approx(5.0)


def test_zero_alpha_stroke_skipped(tmp_path):
    doc = make_doc()
    for st in doc.pages[0].layers[0].strokes:
        st.channels[ir.Channel.ALPHA] = [0.0] * len(st.x)
    root = render(tmp_path, doc)
    # No drawable strokes -> blank page: no groups, no template.
    assert len(list(root)) == 0


def test_remarkable_fixture_end_to_end(tmp_path):
    from inkterop.formats.remarkable import read_page

    page = read_page(FIXTURES / "fineliner-pencil-colors.rm")
    doc = ir.Document(format_id="remarkable", title="fixture", pages=[page])
    out = tmp_path / "fixture.svg"
    SvgWriter().write(doc, out, Fidelity.EXACT)
    root = ET.parse(out).getroot()
    assert len(root.findall(".//svg:path", NS)) > 0


def test_native_fidelity_smoke(tmp_path):
    root = render(tmp_path, fidelity=Fidelity.NATIVE)
    assert len(root.findall(".//svg:path", NS)) > 0
    # NATIVE restyles the fineliner from defaults (constant width, black
    # base color survives as the stroke's semantic color).
    stroked = [p for p in root.findall(".//svg:path", NS) if p.get("stroke")]
    assert stroked


def test_raw_fidelity_rejected(tmp_path):
    with pytest.raises(ValueError, match="raw ink dynamics"):
        SvgWriter().write(make_doc(), tmp_path / "x.svg", Fidelity.RAW)
