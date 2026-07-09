"""Saber (.sba/.sbn2) reader tests against a self-generated fixture."""
from __future__ import annotations

from pathlib import Path

import pytest

from inkterop import ir
from inkterop.formats.saber import SaberReader
from inkterop.formats.saber.reader import parse_bson

FIXTURE = Path(__file__).parent / "fixtures" / "saber" / \
    "saber-mac-pens-text.sba"


def test_parse_bson_subset():
    import struct
    inner = b"\x10n\x00\x2a\x00\x00\x00"  # int32 "n" = 42
    doc = struct.pack("<i", 4 + len(inner) + 1) + inner + b"\x00"
    parsed, end = parse_bson(doc)
    assert parsed == {"n": 42}
    assert end == len(doc)


def test_detect():
    reader = SaberReader()
    assert reader.detect(FIXTURE)
    rm = Path(__file__).parent / "fixtures" / "remarkable" / "ballpoint-small.rm"
    assert not reader.detect(rm)
    gn = Path(__file__).parent / "fixtures" / "goodnotes" / \
        "gn-mac-mixed-pens.goodnotes"
    assert not reader.detect(gn)


def test_read_fixture():
    doc = SaberReader().read(FIXTURE)
    doc.validate()
    assert doc.metadata["sbn_version"] == 19
    assert len(doc.pages) == 2

    strokes = list(doc.pages[0].strokes())
    assert len(strokes) == 4
    families = sorted(s.tool.family.value for s in strokes)
    assert families == ["highlighter", "pen", "pen", "pencil"]

    hl = next(s for s in strokes if s.tool.family is ir.ToolFamily.HIGHLIGHTER)
    assert hl.appearance.underlay is True
    assert hl.appearance.opacity < 0.9  # translucent ARGB alpha
    assert ir.Channel.PRESSURE not in hl.channels  # pe=0 for highlighter

    pencil = next(s for s in strokes if s.tool.family is ir.ToolFamily.PENCIL)
    pressures = pencil.channels[ir.Channel.PRESSURE]
    assert len(pressures) == len(pencil.x)
    assert all(0.0 <= p <= 1.0 for p in pressures)
    assert max(pressures) > 0.05  # real values, not zeros

    # single-dot fountain pen stroke survives
    assert any(len(s) == 1 for s in strokes)

    # typed text (Quill delta) captured
    texts = doc.pages[0].layers[0].texts
    assert any("sadf" in t.text for t in texts)

    # geometry within page bounds
    b = doc.pages[0].bounds
    for s in strokes:
        assert all(b.x_min <= x <= b.x_max for x in s.x)
        assert all(b.y_min <= y <= b.y_max for y in s.y)


def test_fixture_to_pdf(tmp_path):
    from inkterop.convert import convert

    out = tmp_path / "saber.pdf"
    convert(FIXTURE, out)
    assert out.read_bytes()[:5] == b"%PDF-"
