"""reMarkable .rm / .rmdoc writer tests.

Golden render tests are untouched — these only assert IR round-trips
through rmscene write->read.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from inkterop import ir
from inkterop.formats.base import Fidelity
from inkterop.formats.remarkable.reader import RemarkableReader
from inkterop.formats.remarkable.writer import RemarkablePageWriter, RmdocWriter

FIXDIR = Path(__file__).parent / "fixtures" / "remarkable"


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
