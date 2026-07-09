"""Supernote raster writer tests.

Validation story: SupernoteReader parses via supernotelib — an
independent third-party parser — so write->read is a genuine
cross-implementation check (device check still pending).
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from inkterop import ir
from inkterop.formats.base import Fidelity
from inkterop.formats.supernote import SupernoteReader, SupernoteWriter
from inkterop.formats.supernote.writer import BG, BLACK, rle_encode

FIXDIR = Path(__file__).parent / "fixtures" / "supernote"


def test_rle_encode_round_trip():
    """Encode a synthetic row and decode by hand (RATTA_RLE semantics)."""
    row = bytes([BG] * 5 + [BLACK] * 200 + [BG] * 0x4000 + [BLACK])
    enc = rle_encode(row)
    # hand-decode: plain byte L => L+1 pixels; 0xFF => 0x4000 pixels
    out = bytearray()
    for code, ln in zip(enc[::2], enc[1::2]):
        out += bytes([code]) * (0x4000 if ln == 0xFF else ln + 1)
    assert bytes(out) == row


def _ink_page() -> ir.Page:
    diag = ir.Stroke(
        x=[100.0, 500.0], y=[100.0, 500.0],
        tool=ir.ToolRef(family=ir.ToolFamily.PEN),
        color=ir.Color(0.0, 0.0, 0.0),
        channels={ir.Channel.WIDTH: [8.0, 8.0]},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT, width=8.0,
            color=ir.Color(0.0, 0.0, 0.0), opacity=1.0,
        ),
    )
    return ir.Page(bounds=ir.Rect(0.0, 0.0, 702.0, 936.0), point_scale=1.0,
                   layers=[ir.Layer(strokes=[diag])])


def _page_image(page: ir.Page) -> Image.Image:
    raster = page.layers[0].raster
    assert raster is not None
    return Image.open(io.BytesIO(raster.data)).convert("L")


def test_synthetic_write_read_pixels(tmp_path):
    doc = ir.Document(format_id="test", title="ink",
                      pages=[_ink_page(), _ink_page()])
    out = tmp_path / "ink.note"
    SupernoteWriter().write(doc, out, Fidelity.EXACT)

    back = SupernoteReader().read(out)
    back.validate()
    assert len(back.pages) == 2

    img = _page_image(back.pages[0])
    assert img.size == (1404, 1872)
    px = img.load()
    ink = [(x, y) for y in range(0, 1872, 4) for x in range(0, 1404, 4)
           if px[x, y] < 128]
    assert ink, "no ink pixels found"
    # source page fits 702x936 -> k=2, centered: diagonal 100..500 -> 200..1000
    xs = [p[0] for p in ink]
    ys = [p[1] for p in ink]
    assert 180 <= min(xs) <= 220 and 980 <= max(xs) <= 1020
    assert 180 <= min(ys) <= 220 and 980 <= max(ys) <= 1020


def test_supernote_round_trip_raster(tmp_path):
    """supernote -> supernote goes through the raster-composite path."""
    src = SupernoteReader().read(FIXDIR / "synthetic-two-page.note")
    out = tmp_path / "rt.note"
    SupernoteWriter().write(src, out, Fidelity.EXACT)

    back = SupernoteReader().read(out)
    back.validate()
    assert len(back.pages) == len(src.pages)

    # page 1's black square (300,400)-(700,800) must survive two RLE trips
    a = _page_image(src.pages[0])
    b = _page_image(back.pages[0])
    assert b.size == a.size
    assert b.getpixel((500, 600)) < 100  # inside the square: ink
    assert b.getpixel((100, 100)) > 200  # margin: background


def test_landscape_orientation(tmp_path):
    page = ir.Page(bounds=ir.Rect(0.0, 0.0, 936.0, 702.0), point_scale=1.0,
                   layers=[ir.Layer(strokes=[ir.Stroke(
                       x=[100.0, 800.0], y=[350.0, 350.0],
                       tool=ir.ToolRef(family=ir.ToolFamily.PEN),
                       color=ir.Color(0, 0, 0),
                       channels={ir.Channel.WIDTH: [6.0, 6.0]},
                   )])])
    doc = ir.Document(format_id="test", title="land", pages=[page])
    out = tmp_path / "land.note"
    SupernoteWriter().write(doc, out, Fidelity.EXACT)
    back = SupernoteReader().read(out)
    assert back.pages[0].bounds.width > back.pages[0].bounds.height


def test_raw_fidelity_raises(tmp_path):
    doc = ir.Document(format_id="test", title="x", pages=[_ink_page()])
    with pytest.raises(ValueError, match="raster"):
        SupernoteWriter().write(doc, tmp_path / "x.note", Fidelity.RAW)


def test_writer_experimental_gate(tmp_path):
    from inkterop.convert import ConvertError, convert

    with pytest.raises(ConvertError, match="experimental"):
        convert(FIXDIR / "synthetic-two-page.note", tmp_path / "g.note")
