"""Excalidraw (.excalidraw JSON) reader + writer tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from inkterop import ir
from inkterop.formats.base import Fidelity
from inkterop.formats.excalidraw import ExcalidrawReader, ExcalidrawWriter

FIXTURE = Path(__file__).parent / "fixtures" / "excalidraw" / \
    "scribble.excalidraw"


def test_detect():
    reader = ExcalidrawReader()
    assert reader.detect(FIXTURE)
    saber = Path(__file__).parent / "fixtures" / "saber" / \
        "saber-mac-pens-text.sba"
    assert not reader.detect(saber)


def test_read_fixture():
    doc = ExcalidrawReader().read(FIXTURE)
    doc.validate()
    assert len(doc.pages) == 1
    strokes = list(doc.pages[0].strokes())
    # 2 freedraw (deleted one skipped) + rectangle outline
    assert len(strokes) == 3

    fd1 = strokes[0]
    assert fd1.tool.family is ir.ToolFamily.PEN
    assert fd1.x == pytest.approx([100, 120, 140, 160, 180])
    assert fd1.y == pytest.approx([100, 110, 100, 110, 100])
    assert fd1.channels[ir.Channel.PRESSURE] == pytest.approx(
        [0.2, 0.4, 0.6, 0.8, 1.0])

    fd2 = strokes[1]
    assert ir.Channel.PRESSURE not in fd2.channels  # simulatePressure
    assert fd2.appearance.opacity == pytest.approx(0.6)
    assert fd2.color.r > 0.8  # #e03131

    rect = strokes[2]
    assert rect.tool.native.tool_id == "rectangle"
    assert len(rect.x) == 5 and rect.x[0] == rect.x[-1]  # closed outline

    texts = doc.pages[0].layers[0].texts
    assert len(texts) == 1 and texts[0].text == "hello ink"

    # content-bbox bounds contain everything
    b = doc.pages[0].bounds
    for s in strokes:
        assert all(b.x_min <= x <= b.x_max for x in s.x)
        assert all(b.y_min <= y <= b.y_max for y in s.y)


def test_write_read_round_trip(tmp_path):
    src = ExcalidrawReader().read(FIXTURE)
    out = tmp_path / "rt.excalidraw"
    ExcalidrawWriter().write(src, out, Fidelity.EXACT)

    scene = json.loads(out.read_text())
    assert scene["type"] == "excalidraw"
    assert all(not el["isDeleted"] for el in scene["elements"])

    back = ExcalidrawReader().read(out)
    back.validate()
    bs = list(back.pages[0].strokes())
    assert len(bs) == 3  # all strokes re-emitted as freedraw
    # geometry survives (bounds rebase shifts origin; compare extents)
    ss = list(src.pages[0].strokes())
    for a, b in zip(ss, bs):
        assert len(b) == len(a)
        assert (max(b.x) - min(b.x)) == pytest.approx(
            max(a.x) - min(a.x), abs=1e-6)
    # explicit pressures survive raw
    assert bs[0].channels[ir.Channel.PRESSURE] == pytest.approx(
        [0.2, 0.4, 0.6, 0.8, 1.0])
    assert bs[0].appearance.opacity == pytest.approx(1.0)
    assert bs[1].appearance.opacity == pytest.approx(0.6, abs=0.01)
    texts = back.pages[0].layers[0].texts
    assert len(texts) == 1 and texts[0].text == "hello ink"


def test_foreign_conversion(tmp_path):
    """reMarkable -> excalidraw exercises variable-width + raw channels."""
    from inkterop.convert import convert

    rm = Path(__file__).parent / "fixtures" / "remarkable" / \
        "fineliner-pencil-colors.rm"
    out = tmp_path / "rm.excalidraw"
    convert(rm, out, experimental=True)
    doc = ExcalidrawReader().read(out)
    assert len(list(doc.pages[0].strokes())) > 0


def test_fixture_to_pdf(tmp_path):
    from inkterop.convert import convert

    out = tmp_path / "ex.pdf"
    convert(FIXTURE, out)
    assert out.read_bytes()[:5] == b"%PDF-"


def test_writer_validated_no_gate(tmp_path):
    """Writer is validated (docs/validated-writes.md row 2026-07-09):
    conversion must work without --experimental."""
    from inkterop.convert import convert

    out = tmp_path / "ungated.excalidraw"
    convert(FIXTURE, out)
    assert json.loads(out.read_text())["type"] == "excalidraw"


def test_width_law_round_trip(tmp_path):
    """strokeWidth encodes through the measured freedraw rendering law:
    a written file re-read yields the same rendered widths."""
    from statistics import median

    rm = Path(__file__).parent / "fixtures" / "remarkable" / \
        "fineliner-pencil-colors.rm"
    from inkterop.formats.remarkable.reader import RemarkableReader

    src = RemarkableReader().read(rm)
    out = tmp_path / "law.excalidraw"
    ExcalidrawWriter().write(src, out, Fidelity.EXACT)

    back = ExcalidrawReader().read(out)
    ss = list(src.pages[0].strokes())
    bs = list(back.pages[0].strokes())
    assert len(bs) == len(ss)
    from inkterop.formats._scale import unit_factor
    from inkterop.formats.excalidraw import PX_SCALE

    k = unit_factor(src.pages[0], PX_SCALE)  # source units -> written px
    for a, b in zip(ss, bs):
        wa = a.channels.get(ir.Channel.WIDTH)
        wb = b.channels.get(ir.Channel.WIDTH)
        if not wa or not wb:
            continue
        # the widest point maps through p=1.0 exactly; narrower points
        # may clamp at the law's floor, so compare the maximum
        assert max(wb) == pytest.approx(max(wa) * k, rel=0.02)
