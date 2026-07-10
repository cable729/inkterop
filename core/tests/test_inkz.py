"""Tests for the .inkz notebook container (manifest + UIM ink parts +
content-addressed blobs + per-stroke overlay).

The container must be LOSSLESS for the IR (up to the UIM part's float32 /
color-byte quantization) — unlike renderers it may not drop invisible
layers, native payloads, or attachments.
"""
from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

import pytest

from inkterop import formats, ir
from inkterop.formats.base import Fidelity
from inkterop.formats.inkz import InkzReader, InkzWriter

FIXTURES = Path(__file__).parent / "fixtures"

pytest.importorskip("inkterop.formats.uim", reason="uim module required")
# encode_uim is the writer half; skip cleanly while it doesn't exist yet.
uim_mod = __import__("inkterop.formats.uim", fromlist=["encode_uim"])
if not hasattr(uim_mod, "encode_uim"):
    pytest.skip("encode_uim not implemented yet", allow_module_level=True)


def _synthetic_doc() -> ir.Document:
    pen = ir.Stroke(
        x=[10.0, 20.0, 30.0], y=[15.0, 25.0, 20.0],
        tool=ir.ToolRef(ir.ToolFamily.PEN,
                        ir.NativeTool("remarkable", "17",
                                      {"thickness_scale": 1.5})),
        color=ir.Color(0.2, 0.4, 0.6),
        channels={
            ir.Channel.WIDTH: [2.0, 3.0, 2.5],
            ir.Channel.PRESSURE: [0.3, 0.9, 0.5],
        },
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_VARIABLE,
            color=ir.Color(0.2, 0.4, 0.6)),
        extra={"remarkable": {"seq": 7}},
    )
    marker = ir.Stroke(
        x=[5.0, 50.0], y=[40.0, 40.0],
        tool=ir.ToolRef(ir.ToolFamily.HIGHLIGHTER, None),
        color=ir.Color(1.0, 0.9, 0.0),
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT,
            color=ir.Color(1.0, 0.9, 0.0), width=12.0, opacity=0.4,
            blend=ir.BlendMode.DARKEN, underlay=True),
    )
    hidden = ir.Stroke(
        x=[1.0, 2.0], y=[1.0, 2.0],
        tool=ir.ToolRef(ir.ToolFamily.PENCIL, None),
        color=ir.Color(0, 0, 0),
        channels={ir.Channel.ALPHA: [0.5, 0.7]},
    )
    png_1px = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c626001000000ffff03000006000557bfabd40000000049"
        "454e44ae426082")
    page1 = ir.Page(
        bounds=ir.Rect(0, 0, 100, 140), point_scale=1.0,
        layers=[
            ir.Layer(strokes=[pen, marker], name="ink",
                     texts=[ir.TextBlock(12.0, 34.0, "hello", 11.0,
                                         ir.Color(0, 0, 0))]),
            ir.Layer(strokes=[hidden], name="drafts", visible=False),
        ],
        background=ir.TemplateBackground("dots", "P Dots S", pitch=33.0,
                                         dot_radius=0.9, gray=0.6),
    )
    page2 = ir.Page(
        bounds=ir.Rect(0, 0, 100, 140), point_scale=1.0,
        layers=[ir.Layer(
            strokes=[], name="bg",
            raster=ir.RasterImage(png_1px, "png",
                                  ir.Rect(0, 0, 50, 50)))],
        background=ir.PdfBackground("doc.pdf", 2),
    )
    page3 = ir.Page(
        bounds=ir.Rect(0, 0, 100, 140), point_scale=1.0,
        layers=[ir.Layer(strokes=[])],
        background=ir.ColorBackground(ir.Color(0.96, 0.94, 0.9)),
    )
    return ir.Document(
        format_id="test", title="inkz-synthetic",
        pages=[page1, page2, page3],
        attachments={"doc.pdf": b"%PDF-1.4 fake attachment"},
        metadata={"author": "tests"},
        extra={"test": {"k": 1}},
    )


def _roundtrip(doc: ir.Document, tmp: Path) -> ir.Document:
    out = tmp / "doc.inkz"
    InkzWriter().write(doc, out, Fidelity.EXACT)
    assert InkzReader().detect(out)
    got = InkzReader().read(out)
    got.validate()
    return got


def _assert_points_close(a: ir.Stroke, b: ir.Stroke, tol: float = 1e-3):
    assert len(a) == len(b)
    for va, vb in zip(a.x, b.x):
        assert abs(va - vb) <= tol
    for va, vb in zip(a.y, b.y):
        assert abs(va - vb) <= tol


def test_synthetic_roundtrip(tmp_path: Path) -> None:
    doc = _synthetic_doc()
    got = _roundtrip(doc, tmp_path)

    assert got.title == doc.title
    assert got.metadata == doc.metadata
    assert got.extra == doc.extra
    assert got.attachments["doc.pdf"] == b"%PDF-1.4 fake attachment"
    assert len(got.pages) == 3

    p1 = got.pages[0]
    assert isinstance(p1.background, ir.TemplateBackground)
    assert p1.background.name == "P Dots S"
    assert [layer.name for layer in p1.layers] == ["ink", "drafts"]
    assert p1.layers[1].visible is False
    assert len(p1.layers[1].strokes) == 1  # invisible layers survive

    pen, marker = p1.layers[0].strokes
    src_pen, src_marker = doc.pages[0].layers[0].strokes
    _assert_points_close(pen, src_pen)
    assert pen.tool.family == ir.ToolFamily.PEN
    assert pen.tool.native.params == {"thickness_scale": 1.5}
    assert pen.extra == {"remarkable": {"seq": 7}}
    for va, vb in zip(pen.channels[ir.Channel.WIDTH],
                      src_pen.channels[ir.Channel.WIDTH]):
        assert abs(va - vb) <= 1e-2
    assert ir.Channel.PRESSURE in pen.channels

    assert marker.appearance is not None
    assert marker.appearance.underlay is True
    assert marker.appearance.blend == ir.BlendMode.DARKEN
    assert abs(marker.appearance.width - 12.0) <= 1e-2
    assert abs(marker.appearance.opacity - 0.4) <= 1e-6

    t = p1.layers[0].texts[0]
    assert (t.text, t.font_size) == ("hello", 11.0)

    p2 = got.pages[1]
    assert isinstance(p2.background, ir.PdfBackground)
    assert (p2.background.attachment_key, p2.background.page_index) == ("doc.pdf", 2)
    assert p2.layers[0].raster is not None
    assert p2.layers[0].raster.format == "png"

    p3 = got.pages[2]
    assert isinstance(p3.background, ir.ColorBackground)


def test_deterministic_bytes(tmp_path: Path) -> None:
    doc = _synthetic_doc()
    InkzWriter().write(doc, tmp_path / "a.inkz", Fidelity.EXACT)
    InkzWriter().write(doc, tmp_path / "b.inkz", Fidelity.EXACT)
    assert (tmp_path / "a.inkz").read_bytes() == (tmp_path / "b.inkz").read_bytes()


def test_blob_dedup(tmp_path: Path) -> None:
    doc = _synthetic_doc()
    # same attachment content under two keys -> one blob
    doc.attachments["copy.pdf"] = b"%PDF-1.4 fake attachment"
    out = tmp_path / "doc.inkz"
    InkzWriter().write(doc, out, Fidelity.EXACT)
    with zipfile.ZipFile(out) as z:
        blob_names = [n for n in z.namelist() if n.startswith("blobs/")]
    # 1 shared pdf blob + 1 raster png
    assert len(blob_names) == 2


def test_remarkable_fixture_roundtrip(tmp_path: Path) -> None:
    fx = FIXTURES / "remarkable" / "fineliner-pencil-colors.rm"
    doc = formats.reader_for(fx).read(fx)
    got = _roundtrip(doc, tmp_path)
    src_strokes = [s for p in doc.pages for l in p.layers for s in l.strokes]
    got_strokes = [s for p in got.pages for l in p.layers for s in l.strokes]
    assert len(got_strokes) == len(src_strokes)
    # geometry within the UIM part's quantization, in source units
    for a, b in zip(got_strokes, src_strokes):
        assert len(a) == len(b)
        span = max(max(b.x) - min(b.x), 1.0)
        assert abs(a.x[0] - b.x[0]) <= max(1e-3 * span, 1e-2)
        if ir.Channel.WIDTH in b.channels:
            assert ir.Channel.WIDTH in a.channels


def test_pinned_render_via_container(tmp_path: Path) -> None:
    """The whole point: pinned_render(inkz(doc)) == pinned_render(doc)."""
    from PIL import Image

    from inkterop.visual.diff import compare
    from inkterop.visual.png import PngWriter

    fx = FIXTURES / "remarkable" / "fineliner-pencil-colors.rm"
    doc = formats.reader_for(fx).read(fx)
    got = _roundtrip(doc, tmp_path)
    PngWriter().write(doc, tmp_path / "direct.png", Fidelity.EXACT, {"dpi": 96})
    PngWriter().write(got, tmp_path / "via.png", Fidelity.EXACT, {"dpi": 96})
    r = compare(Image.open(tmp_path / "direct.png"),
                Image.open(tmp_path / "via.png"), mode="strict")
    assert r.ink_match_ratio >= 0.999, (
        f"render through container drifted: {r.ink_match_ratio:.4%}")


def test_detect_rejects_foreign_zips(tmp_path: Path) -> None:
    gn = FIXTURES / "goodnotes" / "gn-mac-mixed-pens.goodnotes"
    assert InkzReader().detect(gn) is False
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("hello.txt", "hi")
    assert InkzReader().detect(plain) is False


def test_registry_roundtrip(tmp_path: Path) -> None:
    out = tmp_path / "doc.inkz"
    InkzWriter().write(_synthetic_doc(), out, Fidelity.EXACT)
    r = formats.reader_for(out)
    assert r is not None and r.format_id == "inkz"
    w = formats.writer_for(out)
    assert w is not None and w.format_id == "inkz"
