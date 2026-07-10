"""reMarkable .rm / .rmdoc writer tests.

Golden render tests are untouched — these assert IR round-trips through
rmscene write->read, plus render-level round-trip identity (op dumps via
the golden test's normalization) and foreign-mapping visual fidelity.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from inkterop import ir
from inkterop.formats.base import Fidelity
from inkterop.formats.remarkable.reader import RemarkableReader, read_page
from inkterop.formats.remarkable.writer import (
    RemarkablePageWriter,
    RmdocWriter,
    write_rm_page,
)
from inkterop.render import RenderConfig, render_document

from test_golden_remarkable import pdf_ops

FIXDIR = Path(__file__).parent / "fixtures" / "remarkable"
MANIFEST = json.loads((FIXDIR / "manifest.json").read_text())


def _render_page(page: ir.Page, landscape: bool, out: Path) -> Path:
    doc = ir.Document(
        format_id="remarkable",
        orientation="landscape" if landscape else "portrait",
        pages=[page],
    )
    render_document(doc, out, RenderConfig())
    return out


@pytest.mark.parametrize("slug", sorted(MANIFEST))
def test_round_trip_render_ops_identical(tmp_path, slug):
    """fixture -> IR -> write .rm -> IR must render op-identically."""
    info = MANIFEST[slug]
    landscape = info["orientation"] == "landscape"
    src = read_page(FIXDIR / info["file"], landscape=landscape,
                    template=info["template"])
    rt = tmp_path / "rt.rm"
    write_rm_page(src, rt)
    back = read_page(rt, landscape=landscape, template=info["template"])

    ops_a = pdf_ops(_render_page(src, landscape, tmp_path / "a.pdf"))
    ops_b = pdf_ops(_render_page(back, landscape, tmp_path / "b.pdf"))
    assert ops_a == ops_b


def test_round_trip_render_pixels_identical(tmp_path):
    """Pixel-level variant on the fixture used for the app-open check."""
    from inkterop.visual.diff import compare
    from inkterop.visual.raster import pdf_pages_to_images

    src = read_page(FIXDIR / "highlighter-marker-pencil.rm")
    rt = tmp_path / "rt.rm"
    write_rm_page(src, rt)
    back = read_page(rt)
    a = pdf_pages_to_images(
        _render_page(src, False, tmp_path / "a.pdf"), dpi=96)[0]
    b = pdf_pages_to_images(
        _render_page(back, False, tmp_path / "b.pdf"), dpi=96)[0]
    r = compare(a, b, mode="strict", make_diff_image=False)
    assert r.n_diff_pixels == 0


def test_saber_to_rm_renders_like_saber(tmp_path):
    """Foreign mapping: saber -> .rm must not restyle into artifacts.

    Guards the three app-open failures: hollow/doubled pen (raw foreign
    pressure), sparse-dot pencil (same), solid saturated highlighter
    (opacity lost + palette matched at the wrong scale, PenColor.BLACK).
    Measured 0.946 ink-match after the fixes (0.041 before, 96 dpi
    registered mode); 0.90 leaves margin for raster jitter while any one
    artifact returning drops the score far below it.
    """
    from inkterop.formats.saber.reader import SaberReader
    from inkterop.visual.diff import compare
    from inkterop.visual.raster import pdf_pages_to_images

    saber = SaberReader().read(
        FIXDIR.parent / "saber" / "saber-mac-pens-text.sba")
    doc = ir.Document(format_id="saber", title="p0", pages=[saber.pages[0]])

    a_pdf = tmp_path / "direct.pdf"
    render_document(doc, a_pdf, RenderConfig())

    rm = tmp_path / "conv.rm"
    RemarkablePageWriter().write(doc, rm, Fidelity.EXACT)
    conv = RemarkableReader().read(rm)
    b_pdf = tmp_path / "conv.pdf"
    render_document(conv, b_pdf, RenderConfig())

    a = pdf_pages_to_images(a_pdf, dpi=96)[0]
    b = pdf_pages_to_images(b_pdf, dpi=96)[0]
    r = compare(a, b, mode="registered", make_diff_image=False)
    assert r.aspect_warning is None, r.aspect_warning
    assert r.ink_match_ratio >= 0.90, f"ink match {r.ink_match_ratio:.4f}"

    # the specific bad mappings, at the block level
    from rmscene import read_blocks
    from rmscene import scene_items as si
    from rmscene.scene_stream import SceneLineItemBlock

    with open(rm, "rb") as f:
        lines = [b.item.value for b in read_blocks(f)
                 if isinstance(b, SceneLineItemBlock) and b.item.value]
    hl = next(ln for ln in lines if ln.tool is si.Pen.HIGHLIGHTER_2)
    assert hl.color is not si.PenColor.BLACK  # palette scale bug
    assert hl.color_rgba[3] == 255  # opacity baked into rgb, not dropped
    assert hl.color_rgba[2] > 128  # composited over white, not saturated
    pencil = next(ln for ln in lines if ln.tool is si.Pen.PENCIL_2)
    assert min(p.pressure for p in pencil.points) >= 200  # synthesized


@pytest.mark.parametrize("name", [
    "ballpoint-small.rm",
    "fineliner-pencil-colors.rm",
    "highlighter-marker-pencil.rm",
    "calligraphy-marker-paintbrush-shader.rm",
])
def test_same_format_round_trip(tmp_path, name):
    """read fixture -> write .rm -> read: geometry and channels identical."""
    src = RemarkableReader().read(FIXDIR / name)
    out = tmp_path / "rt.rm"
    RemarkablePageWriter().write(src, out, Fidelity.EXACT)

    assert RemarkableReader().detect(out)
    back = RemarkableReader().read(out)
    back.validate()

    ss = list(src.pages[0].strokes())
    bs = list(back.pages[0].strokes())
    assert len(bs) == len(ss)
    for a, b in zip(ss, bs):
        assert b.tool.native.tool_id == a.tool.native.tool_id
        assert b.tool.native.params["color"] == a.tool.native.params["color"]
        assert b.tool.native.params["thickness_scale"] == pytest.approx(
            a.tool.native.params["thickness_scale"])
        assert len(b) == len(a)
        # device space preserved exactly (f32 storage)
        assert b.x == pytest.approx(a.x, abs=1e-3)
        assert b.y == pytest.approx(a.y, abs=1e-3)
        # ints round-trip exactly: width (/4 rule), pressure, speed, azimuth
        assert b.channels[ir.Channel.WIDTH] == pytest.approx(
            a.channels[ir.Channel.WIDTH])
        assert b.channels[ir.Channel.PRESSURE] == pytest.approx(
            a.channels[ir.Channel.PRESSURE])
        assert b.channels[ir.Channel.SPEED] == pytest.approx(
            a.channels[ir.Channel.SPEED], abs=0.5)
        assert b.channels[ir.Channel.TILT_AZIMUTH] == pytest.approx(
            a.channels[ir.Channel.TILT_AZIMUTH], abs=0.03)
        # appearance regenerated identically by the same PenModel
        assert b.appearance.mode is a.appearance.mode
        assert b.appearance.opacity == pytest.approx(a.appearance.opacity,
                                                     abs=1 / 255)


def test_foreign_document_fits_canvas(tmp_path):
    """A letter-sized foreign doc lands centered on the rM canvas."""
    s = ir.Stroke(
        x=[0.0, 612.0], y=[0.0, 100.0],
        tool=ir.ToolRef(family=ir.ToolFamily.FINELINER),
        color=ir.Color(0.2, 0.4, 0.6),
        channels={ir.Channel.WIDTH: [3.0, 3.0]},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT, width=3.0,
            color=ir.Color(0.2, 0.4, 0.6), opacity=1.0,
        ),
    )
    doc = ir.Document(format_id="test", title="foreign", pages=[
        ir.Page(bounds=ir.Rect(0.0, 0.0, 612.0, 792.0), point_scale=1.0,
                layers=[ir.Layer(strokes=[s])]),
    ])
    out = tmp_path / "foreign.rm"
    RemarkablePageWriter().write(doc, out, Fidelity.EXACT)
    back = RemarkableReader().read(out)
    bs = list(back.pages[0].strokes())
    assert len(bs) == 1
    # 612pt width -> 1620u canvas, x centered on 0
    assert bs[0].x[0] == pytest.approx(-810.0, abs=0.01)
    assert bs[0].x[1] == pytest.approx(810.0, abs=0.01)
    assert bs[0].y[0] == pytest.approx(0.0, abs=0.01)
    # width scaled by the same factor (3pt * 1620/612), /4-rule quantized
    k = 1620.0 / 612.0
    assert bs[0].channels[ir.Channel.WIDTH][0] == pytest.approx(
        3.0 * k, abs=0.25)
    assert bs[0].tool.family is ir.ToolFamily.FINELINER


def test_multi_layer_preserved(tmp_path):
    def stroke(y):
        return ir.Stroke(
            x=[0.0, 100.0], y=[y, y],
            tool=ir.ToolRef(family=ir.ToolFamily.PEN),
            color=ir.Color(0, 0, 0),
            channels={ir.Channel.WIDTH: [2.0, 2.0]},
        )
    doc = ir.Document(format_id="test", title="layers", pages=[
        ir.Page(bounds=ir.Rect(0.0, 0.0, 500.0, 500.0), point_scale=1.0,
                layers=[ir.Layer(strokes=[stroke(10.0)], name="a"),
                        ir.Layer(strokes=[stroke(20.0), stroke(30.0)], name="b")]),
    ])
    out = tmp_path / "layers.rm"
    RemarkablePageWriter().write(doc, out, Fidelity.EXACT)
    back = RemarkableReader().read(out)
    assert len(list(back.pages[0].strokes())) == 3


def test_multi_page_rm_requires_page_option(tmp_path):
    page = ir.Page(bounds=ir.Rect(0, 0, 100, 100), point_scale=1.0)
    doc = ir.Document(format_id="test", title="two", pages=[page, page])
    with pytest.raises(ValueError, match="one page"):
        RemarkablePageWriter().write(doc, tmp_path / "x.rm", Fidelity.EXACT)
    RemarkablePageWriter().write(doc, tmp_path / "x.rm", Fidelity.EXACT,
                                 options={"page": 1})


def test_rmdoc_container(tmp_path):
    src = RemarkableReader().read(FIXDIR / "ballpoint-small.rm")
    src.pages.append(src.pages[0])  # two pages
    out = tmp_path / "doc.rmdoc"
    RmdocWriter().write(src, out, Fidelity.EXACT)

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        meta_name = next(n for n in names if n.endswith(".metadata"))
        content_name = next(n for n in names if n.endswith(".content"))
        doc_uuid = meta_name.split(".")[0]
        meta = json.loads(zf.read(meta_name))
        content = json.loads(zf.read(content_name))
        assert meta["type"] == "DocumentType"
        assert content["pageCount"] == 2
        page_ids = [p["id"] for p in content["cPages"]["pages"]]
        rm_members = [n for n in names if n.endswith(".rm")]
        assert sorted(rm_members) == sorted(
            f"{doc_uuid}/{pid}.rm" for pid in page_ids)
        # each page parses back
        for m in rm_members:
            data = zf.read(m)
            assert data.startswith(b"reMarkable .lines file")
            p = tmp_path / "page.rm"
            p.write_bytes(data)
            RemarkableReader().read(p).validate()


def test_writer_experimental_gate(tmp_path):
    from inkterop.convert import ConvertError, convert

    with pytest.raises(ConvertError, match="experimental"):
        convert(FIXDIR / "ballpoint-small.rm", tmp_path / "gated.rmdoc")
    convert(FIXDIR / "ballpoint-small.rm", tmp_path / "ok.rmdoc",
            experimental=True)
    assert (tmp_path / "ok.rmdoc").exists()
