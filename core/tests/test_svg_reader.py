"""SVG readers: generic subset, SvgWriter round-trip, Stylus Labs Write."""
from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from inkterop import ir
from inkterop.formats.base import Fidelity
from inkterop.formats.inkml import InkmlWriter
from inkterop.formats.remarkable.reader import RemarkableReader
from inkterop.formats.svg import SvgReader, WriteReader
from inkterop.formats.svg.reader import (
    IDENTITY,
    UnsupportedPath,
    mat_apply,
    parse_path,
    parse_transform,
)
from inkterop.render.svg import SvgWriter

FIXTURES = Path(__file__).parent / "fixtures" / "svg"
RM_FIXTURES = Path(__file__).parent / "fixtures" / "remarkable"
SABER_FIXTURE = (Path(__file__).parent / "fixtures" / "saber"
                 / "saber-mac-pens-text.sba")
WRITE_CORPUS = (Path(__file__).parents[2] / "corpus" / "third-party"
                / "styluslabs-write")

needs_write_corpus = pytest.mark.skipif(
    not WRITE_CORPUS.exists(), reason="styluslabs-write corpus not present"
)


# --- path data parsing -------------------------------------------------------

def test_parse_path_absolute_commands():
    (pts, closed), = parse_path("M 10 10 L 50 10 H 60 V 20")
    assert not closed
    assert pts == [(10, 10), (50, 10), (60, 10), (60, 20)]


def test_parse_path_relative_and_implicit_repetition():
    # Write-style: absolute moveto then repeated implicit relative linetos.
    (pts, closed), = parse_path("M100 100 l10 -20 10 20")
    assert pts == [(100, 100), (110, 80), (120, 100)]
    assert not closed


def test_parse_path_implicit_lineto_after_absolute_m():
    (pts, _), = parse_path("M 1 2 3 4 5 6")
    assert pts == [(1, 2), (3, 4), (5, 6)]


def test_parse_path_closepath_and_multiple_subpaths():
    subs = parse_path("M0 0 L10 0 L10 10 Z M20 20 l5 0")
    assert len(subs) == 2
    assert subs[0][1] is True
    assert subs[0][0][-1] == (10, 10)  # Z does not duplicate the start point
    assert subs[1] == ([(20, 20), (25, 20)], False)


def test_parse_path_cubic_flattening():
    (pts, _), = parse_path("M 0 0 C 0 10 10 10 10 0")
    assert len(pts) == 17  # start + 16 segments
    assert pts[-1] == pytest.approx((10, 0))
    assert pts[8][0] == pytest.approx(5.0)  # symmetric midpoint
    assert pts[8][1] == pytest.approx(7.5)  # cubic peak 3/4 h


def test_parse_path_quadratic_flattening():
    (pts, _), = parse_path("M 0 0 Q 5 10 10 0")
    assert len(pts) == 17
    assert pts[8][1] == pytest.approx(5.0)  # quadratic peak h/2


def test_parse_path_arc_rejected():
    with pytest.raises(UnsupportedPath):
        parse_path("M 0 0 A 10 10 0 0 1 20 0")


def test_parse_path_smooth_rejected():
    with pytest.raises(UnsupportedPath):
        parse_path("M 0 0 C 0 1 1 1 1 0 S 3 -1 3 0")


# --- transforms --------------------------------------------------------------

def test_transform_translate_scale():
    m = parse_transform("translate(10,20) scale(2)")
    assert mat_apply(m, 5, 5) == pytest.approx((20, 30))


def test_transform_matrix():
    m = parse_transform("matrix(0 1 -1 0 10 0)")  # rotate 90 + translate
    assert mat_apply(m, 1, 0) == pytest.approx((10, 1))


def test_transform_rotate_about_point():
    m = parse_transform("rotate(90 10 10)")
    assert mat_apply(m, 20, 10) == pytest.approx((10, 20))


def test_transform_stack_order():
    left = parse_transform("translate(100,0)")
    m = parse_transform("scale(2)")
    from inkterop.formats.svg.reader import mat_mul
    assert mat_apply(mat_mul(left, m), 1, 1) == pytest.approx((102, 2))
    assert mat_apply(mat_mul(m, left), 1, 1) == pytest.approx((202, 2))


def test_transform_unknown_op_ignored():
    m = parse_transform("skewX(30) translate(1,2)")
    assert mat_apply(m, 0, 0) == pytest.approx((1, 2))
    assert parse_transform("") == IDENTITY


# --- detection ---------------------------------------------------------------

def test_detect_discrimination(tmp_path):
    generic = FIXTURES / "tiny-generic.svg"
    write = FIXTURES / "write-mini.svg"
    svg_reader, write_reader = SvgReader(), WriteReader()

    # Generic accepts ANY svg (registry order gives WriteReader precedence).
    assert svg_reader.detect(generic)
    assert svg_reader.detect(write)
    assert write_reader.detect(write)
    assert not write_reader.detect(generic)

    # Non-SVG formats rejected.
    assert not svg_reader.detect(SABER_FIXTURE)
    assert not write_reader.detect(SABER_FIXTURE)
    inkml = tmp_path / "sample.inkml"
    InkmlWriter().write(_tiny_doc(), inkml, Fidelity.RAW)
    assert not svg_reader.detect(inkml)
    assert not write_reader.detect(inkml)


def test_detect_gzipped(tmp_path):
    for name, reader, other in (
        ("tiny-generic.svg", SvgReader(), WriteReader()),
        ("write-mini.svg", WriteReader(), None),
    ):
        gz = tmp_path / (name + "z")
        gz.write_bytes(gzip.compress((FIXTURES / name).read_bytes()))
        assert reader.detect(gz)
        assert reader.read(gz).pages
        if other is not None:
            assert not other.detect(gz)


def _tiny_doc() -> ir.Document:
    stroke = ir.Stroke(
        x=[0.0, 1.0], y=[0.0, 1.0],
        tool=ir.ToolRef(ir.ToolFamily.PEN), color=ir.Color(0, 0, 0),
    )
    return ir.Document(format_id="test", pages=[
        ir.Page(bounds=ir.Rect(0, 0, 10, 10), point_scale=1.0,
                layers=[ir.Layer(strokes=[stroke])])
    ])


# --- generic fixture read ----------------------------------------------------

def test_tiny_generic_read():
    doc = SvgReader().read(FIXTURES / "tiny-generic.svg")
    doc.validate()
    assert doc.format_id == "svg"
    assert len(doc.pages) == 1
    page = doc.pages[0]
    assert (page.bounds.width, page.bounds.height) == (200.0, 150.0)
    assert page.point_scale == pytest.approx(1.0)  # width in pt == viewBox

    strokes = list(page.strokes())
    assert len(strokes) == 5  # fill-only + arc paths skipped

    # 1: M/L/H/V path.
    s1 = strokes[0]
    assert list(zip(s1.x, s1.y)) == [(10, 10), (50, 10), (60, 10), (60, 20),
                                     (70, 25)]
    assert s1.appearance.color.rgb() == pytest.approx((1.0, 0.0, 0.0))
    assert s1.appearance.width == pytest.approx(2.0)
    assert s1.appearance.mode is ir.GeometryMode.STROKED_CONSTANT

    # 2: transform flattened; stroke-width scaled by sqrt(|det|)=2.
    s2 = strokes[1]
    assert (s2.x[0], s2.y[0]) == pytest.approx((20, 30))
    assert (s2.x[-1], s2.y[-1]) == pytest.approx((40, 30))
    assert s2.appearance.width == pytest.approx(2.0)

    # 3: cubic flattened to 17 points; style= wins over presentation attr.
    s3 = strokes[2]
    assert len(s3.x) == 17
    assert s3.appearance.color.rgb() == pytest.approx((0.0, 0.0, 1.0))
    assert s3.appearance.opacity == pytest.approx(0.5)
    assert max(s3.y) <= 100.0 and min(s3.y) >= 85.0  # bowl between endpoints

    # 4: polyline, #0a0.
    s4 = strokes[3]
    assert list(zip(s4.x, s4.y)) == [(120, 10), (130, 20), (140, 10)]
    assert s4.appearance.color.rgb() == pytest.approx((0.0, 2 / 3, 0.0),
                                                      abs=1e-6)

    # 5: line.
    s5 = strokes[4]
    assert list(zip(s5.x, s5.y)) == [(10, 140), (190, 140)]


def test_px_point_scale(tmp_path):
    f = tmp_path / "px.svg"
    f.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100px" '
                 'height="100px" viewBox="0 0 100 100">'
                 '<path d="M0 0 L1 1" stroke="black" fill="none"/></svg>')
    doc = SvgReader().read(f)
    assert doc.pages[0].point_scale == pytest.approx(0.75)

    f2 = tmp_path / "plain.svg"  # unitless == px
    f2.write_text('<svg viewBox="0 0 100 100" width="100">'
                  '<path d="M0 0 L1 1" stroke="black" fill="none"/></svg>')
    assert SvgReader().read(f2).pages[0].point_scale == pytest.approx(0.75)


# --- round-trip through our own SvgWriter ------------------------------------

def test_roundtrip_remarkable_ballpoint(tmp_path):
    doc = RemarkableReader().read(RM_FIXTURES / "ballpoint-small.rm")
    page = doc.pages[0]
    orig = list(page.strokes())
    out = tmp_path / "rt.svg"
    SvgWriter().write(doc, out, Fidelity.EXACT)

    doc2 = SvgReader().read(out)
    doc2.validate()
    page2 = doc2.pages[0]
    back = list(page2.strokes())

    assert len(back) == len(orig)
    assert page2.point_scale == pytest.approx(1.0)  # writer emits pt
    scale = page.point_scale
    x0, y0 = page.bounds.x_min, page.bounds.y_min

    for a, b in zip(orig, back):
        # Tool family and per-point counts survive via data-rmi-* attrs.
        assert b.tool.family is a.tool.family
        assert len(b.x) == len(a.x)
        assert len(b.channels[ir.Channel.PRESSURE]) == len(a.x)
        assert b.channels[ir.Channel.PRESSURE] == pytest.approx(
            a.channels[ir.Channel.PRESSURE], abs=1e-3)  # writer rounds to 3
        # Geometry within tolerance (2-decimal output + midpoint rebuild).
        for xa, ya, xb, yb in zip(a.x, a.y, b.x, b.y):
            assert xb == pytest.approx((xa - x0) * scale, abs=0.05)
            assert yb == pytest.approx((ya - y0) * scale, abs=0.05)
        # Per-point widths come back in page units (pt).
        wa = a.channels[ir.Channel.WIDTH]
        wb = b.channels[ir.Channel.WIDTH]
        for va, vb in zip(wa, wb):
            assert vb == pytest.approx(va * scale, abs=0.08)


def test_roundtrip_synthetic_constant_and_dot(tmp_path):
    doc = _tiny_doc()
    stroke = doc.pages[0].layers[0].strokes[0]
    stroke.channels[ir.Channel.WIDTH] = [3.0, 3.0]
    stroke.channels[ir.Channel.PRESSURE] = [0.25, 0.75]
    stroke.appearance = ir.StrokeAppearance(
        mode=ir.GeometryMode.STROKED_CONSTANT, width=3.0,
        color=ir.Color(0, 0, 0))
    dot = ir.Stroke(
        x=[5.0, 5.0], y=[5.0, 5.0],
        tool=ir.ToolRef(ir.ToolFamily.PENCIL), color=ir.Color(0, 0, 0),
        channels={ir.Channel.WIDTH: [2.0, 4.0],
                  ir.Channel.PRESSURE: [0.5, 0.9]},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_VARIABLE, color=ir.Color(0, 0, 0)),
    )
    doc.pages[0].layers[0].strokes.append(dot)

    out = tmp_path / "syn.svg"
    SvgWriter().write(doc, out, Fidelity.EXACT)
    back = list(SvgReader().read(out).pages[0].strokes())
    assert len(back) == 2

    const = back[0]
    assert const.tool.family is ir.ToolFamily.PEN
    assert const.appearance.mode is ir.GeometryMode.STROKED_CONSTANT
    assert const.channels[ir.Channel.WIDTH] == pytest.approx([3.0, 3.0])
    assert const.channels[ir.Channel.PRESSURE] == pytest.approx([0.25, 0.75])

    dot2 = back[1]  # degenerate stroke round-trips through <circle>
    assert dot2.tool.family is ir.ToolFamily.PENCIL
    assert len(dot2.x) == 2
    assert dot2.x == pytest.approx([5.0, 5.0])
    assert dot2.channels[ir.Channel.WIDTH] == pytest.approx([2.0, 4.0])
    assert dot2.channels[ir.Channel.PRESSURE] == pytest.approx([0.5, 0.9])


def test_roundtrip_layers_and_text(tmp_path):
    doc = _tiny_doc()
    doc.pages[0].layers[0].name = "Layer A"
    doc.pages[0].layers[0].texts.append(
        ir.TextBlock(x=2.0, y=3.0, text="hello"))
    out = tmp_path / "layers.svg"
    SvgWriter().write(doc, out, Fidelity.EXACT)
    page = SvgReader().read(out).pages[0]
    assert [ly.name for ly in page.layers] == ["Layer A"]
    assert page.layers[0].texts[0].text == "hello"
    assert page.layers[0].texts[0].x == pytest.approx(2.0)


# --- Stylus Labs Write -------------------------------------------------------

def test_write_mini_read():
    doc = WriteReader().read(FIXTURES / "write-mini.svg")
    doc.validate()
    assert doc.format_id == "write"
    assert len(doc.pages) == 2

    p1 = doc.pages[0]
    assert (p1.bounds.width, p1.bounds.height) == (768.0, 1050.0)
    assert p1.point_scale == pytest.approx(0.75)
    assert isinstance(p1.background, ir.TemplateBackground)
    assert p1.background.kind == "lines"
    assert p1.background.pitch == pytest.approx(40.0)
    assert p1.extra["write"]["marginLeft"] == "100"
    assert p1.extra["write"]["rulecolor"] == "#7F0000FF"

    strokes = list(p1.strokes())
    assert len(strokes) == 2  # ruleline paths + pagerect skipped
    s1 = strokes[0]
    assert s1.tool.family is ir.ToolFamily.PEN
    assert s1.tool.native.format_id == "write"
    assert s1.tool.native.tool_id == "pen"
    assert list(zip(s1.x, s1.y)) == [(100, 100), (110, 80), (120, 100)]
    assert s1.appearance.width == pytest.approx(3.0)
    s2 = strokes[1]  # inside an <a> hyperlink wrapper
    assert s2.appearance.color.rgb() == pytest.approx((2 / 3, 0, 0), abs=1e-2)

    p2 = doc.pages[1]
    assert isinstance(p2.background, ir.TemplateBackground)
    assert p2.background.kind == "grid"
    assert p2.background.pitch == pytest.approx(35.0)
    (s3,) = list(p2.strokes())
    assert s3.tool.family is ir.ToolFamily.UNKNOWN  # unobserved class
    assert s3.tool.native.tool_id == "highlight"


def test_write_mini_generic_fallback_read():
    # The generic reader must also cope (registry order decides winner).
    doc = SvgReader().read(FIXTURES / "write-mini.svg")
    doc.validate()
    assert len(doc.pages) == 1  # generic = one page per file
    assert list(doc.pages[0].strokes())  # rulelines land as strokes: OK here


def test_write_svgz(tmp_path):
    gz = tmp_path / "mini.svgz"
    gz.write_bytes(gzip.compress((FIXTURES / "write-mini.svg").read_bytes()))
    doc = WriteReader().read(gz)
    assert len(doc.pages) == 2
    assert len(list(doc.pages[0].strokes())) == 2


# --- real Write corpus sample (gated) ----------------------------------------

@needs_write_corpus
def test_write_corpus_site_page():
    doc = WriteReader().read(WRITE_CORPUS / "site1_page002.svg")
    doc.validate()
    assert len(doc.pages) == 1
    page = doc.pages[0]
    assert (page.bounds.width, page.bounds.height) == (900.0, 220.0)
    assert isinstance(page.background, ir.TemplateBackground)
    assert page.background.kind == "lines"
    assert page.background.pitch == pytest.approx(40.0)

    strokes = list(page.strokes())
    # 149 class="write-stroke-pen" paths + 27 unclassed ink paths inside
    # <a class="hyperref"> wrappers (handwritten link text).
    assert len(strokes) == 176
    fams = [s.tool.family for s in strokes]
    assert fams.count(ir.ToolFamily.PEN) == 149
    assert fams.count(ir.ToolFamily.UNKNOWN) == 27
    assert all(len(s.x) >= 2 for s in strokes)
    for s in strokes:  # everything inside the page
        assert 0 <= min(s.x) and max(s.x) <= 900
        assert 0 <= min(s.y) and max(s.y) <= 220


@needs_write_corpus
def test_write_corpus_features_page():
    doc = WriteReader().read(WRITE_CORPUS / "features_page002.svg")
    doc.validate()
    strokes = list(doc.pages[0].strokes())
    # 1275 write-stroke-pen + 47 unclassed hyperlink-ink paths.
    assert len(strokes) == 1322
    fams = [s.tool.family for s in strokes]
    assert fams.count(ir.ToolFamily.PEN) == 1275
