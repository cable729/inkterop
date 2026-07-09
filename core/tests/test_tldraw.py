"""tldraw (.tldr JSON) reader tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from inkterop import formats, ir
from inkterop.formats.tldraw import TldrawReader

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "tldraw" / "two-pages.tldr"

# Not yet in the registry (formats/__init__.py untouched per workstream
# rules); register here so convert() can route .tldr.
if not any(r.format_id == "tldraw" for r in formats.readers()):
    formats.register_reader(TldrawReader())


def test_detect():
    reader = TldrawReader()
    assert reader.detect(FIXTURE)
    # Both are JSON objects — must discriminate on the version marker.
    excalidraw = FIXTURES / "excalidraw" / "scribble.excalidraw"
    assert not reader.detect(excalidraw)
    saber = FIXTURES / "saber" / "saber-mac-pens-text.sba"
    assert not reader.detect(saber)
    assert not reader.detect(FIXTURES / "tldraw" / "does-not-exist.tldr")
    # ...and the excalidraw reader must not claim the .tldr file.
    from inkterop.formats.excalidraw import ExcalidrawReader
    assert not ExcalidrawReader().detect(FIXTURE)


def test_read_fixture():
    doc = TldrawReader().read(FIXTURE)
    doc.validate()
    assert doc.format_id == "tldraw"
    assert len(doc.pages) == 2
    assert doc.pages[0].extra["name"] == "Page 1"
    assert doc.pages[1].extra["name"] == "Page 2"

    # Page 1: pen draw + straight draw + highlight (geo skipped).
    p1 = list(doc.pages[0].strokes())
    assert len(p1) == 3
    assert doc.metadata["skipped_shapes"] == {"geo": 1}

    pen = p1[0]
    assert pen.tool.family is ir.ToolFamily.PEN
    assert pen.tool.native.tool_id == "draw"
    assert pen.x == pytest.approx([100, 120, 140, 160, 180])
    assert pen.y == pytest.approx([100, 110, 100, 110, 100])
    assert pen.channels[ir.Channel.PRESSURE] == pytest.approx(
        [0.2, 0.4, 0.6, 0.8, 1.0])  # z -> PRESSURE
    assert pen.appearance.width == pytest.approx(3.5)  # size "m"
    assert pen.color.r == pytest.approx(0xE0 / 255)  # "red" #e03131
    assert pen.color.g == pytest.approx(0x31 / 255)

    straight = p1[1]
    # constant z=0.5 without isPen is a placeholder, not pressure
    assert ir.Channel.PRESSURE not in straight.channels
    assert straight.x == pytest.approx([200, 300])
    assert straight.appearance.width == pytest.approx(2.0)  # size "s"
    assert straight.color.r == pytest.approx(0x1D / 255)  # "black" #1d1d1d

    hl = p1[2]
    assert hl.tool.family is ir.ToolFamily.HIGHLIGHTER
    assert hl.appearance.underlay
    assert hl.appearance.blend is ir.BlendMode.DARKEN
    assert hl.appearance.width == pytest.approx(5.0)  # size "l"

    texts = doc.pages[0].layers[0].texts
    assert len(texts) == 1
    assert texts[0].text == "hello tldraw"  # from richText tree
    assert texts[0].font_size == pytest.approx(24.0)  # size "m"
    assert texts[0].color.b == pytest.approx(0xE9 / 255)  # "blue" #4465e9

    # Page 2: one xl draw stroke.
    p2 = list(doc.pages[1].strokes())
    assert len(p2) == 1
    assert p2[0].appearance.width == pytest.approx(10.0)  # size "xl"
    assert p2[0].x == pytest.approx([50, 90, 130])

    # content-bbox bounds contain everything on each page
    for page in doc.pages:
        b = page.bounds
        for s in page.strokes():
            assert all(b.x_min <= x <= b.x_max for x in s.x)
            assert all(b.y_min <= y <= b.y_max for y in s.y)
        for t in page.layers[0].texts:
            assert b.x_min <= t.x <= b.x_max
            assert b.y_min <= t.y <= b.y_max


def test_fixture_to_pdf(tmp_path):
    from inkterop.convert import convert

    out = tmp_path / "tldr.pdf"
    convert(FIXTURE, out)
    assert out.read_bytes()[:5] == b"%PDF-"
