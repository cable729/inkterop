"""Nebo (.nebo / BINK v5) reader tests against a self-generated fixture.

Fixture: nebo-ipad-pen-highlighter.nebo — drawn by the repo owner in
Nebo iPad 7.4.3 (CC0): one black pen scribble (280 points, recognized
as "W") and one yellow highlighter stroke (178 points). Expected
geometry cross-checked against the app's own SVG export (mm, A4).
"""
from __future__ import annotations

import struct
from pathlib import Path

from inkterop import ir
from inkterop.formats.nebo import NeboReader
from inkterop.formats.nebo.reader import parse_bink

FIXTURE = Path(__file__).parent / "fixtures" / "nebo" / \
    "nebo-ipad-pen-highlighter.nebo"


def _synthetic_bink() -> bytes:
    """Minimal BINK v5: one 3-point stroke, empty tag table."""
    def s(text):
        return struct.pack("<I", len(text)) + text.encode()

    out = b"BINK\x00" + struct.pack("<IBI", 5, 0, 1)
    out += struct.pack("<I", 2)  # channels
    out += s("X") + b"\x20\x04\x01\x00" + struct.pack("<I", 1) + s("mm")
    out += s("Y") + b"\x20\x04\x01\x00" + struct.pack("<I", 1) + s("mm")
    out += struct.pack("<I", 0)  # empty layout table
    out += struct.pack("<II", 1000, 1000)
    out += struct.pack("<IBI", 3, 0, 1)  # unk, 0, nstrokes=1
    out += struct.pack("<IQffIHI", 0x80000000, 1_783_614_500_000_000,
                       10.0, 20.0, 0x0C4910B9, 0, 3)
    out += struct.pack("<3h", 0, 500, 500)    # dx
    out += struct.pack("<3h", 0, 0, -500)     # dy
    out += bytes([255, 255, 255])             # force
    out += struct.pack("<IIB", 0, 0, 0)       # tag table, 0 records
    return out


def test_parse_bink_synthetic():
    parsed = parse_bink(_synthetic_bink())
    assert parsed["version"] == 5
    assert [c[0] for c in parsed["channels"]] == ["X", "Y"]
    (st,) = parsed["strokes"]
    assert st["x"] == [10.0, 11.0, 12.0]   # 500 units = 1 mm
    assert st["y"] == [20.0, 20.0, 19.0]
    assert st["f"] == [255, 255, 255]
    assert parsed["tags"] == []


def test_detect():
    reader = NeboReader()
    assert reader.detect(FIXTURE)
    other = Path(__file__).parent / "fixtures" / "saber" / \
        "saber-mac-pens-text.sba"
    assert not reader.detect(other)


def test_read_fixture():
    doc = NeboReader().read(FIXTURE)
    doc.validate()
    assert doc.format_id == "nebo"
    assert doc.title == "My folder"
    assert "Nebo/7.4.3" in doc.metadata["application_version"]
    assert len(doc.pages) == 1

    page = doc.pages[0]
    assert (page.bounds.x_max, page.bounds.y_max) == (210.0, 297.0)  # A4 mm
    strokes = list(page.strokes())
    assert [len(s.x) for s in strokes] == [280, 178]

    pen, hl = strokes
    assert pen.tool.family is ir.ToolFamily.PEN
    assert pen.color == ir.Color(0.0, 0.0, 0.0)
    assert pen.appearance.opacity == 1.0
    assert pen.extra["nebo"]["t0_us"] == 1783614500784078
    assert pen.tool.native.params["pressure_sensitivity"] == 0.57
    assert set(pen.channels[ir.Channel.PRESSURE]) == {1.0}
    # bbox vs the app's own SVG export (outline polygons, so allow the
    # pen halfwidth ~0.2mm): svg path bbox (14.63, 11.64)-(48.11, 50.94)
    assert abs(min(pen.x) - 14.8) < 0.5 and abs(max(pen.x) - 48.1) < 0.5
    assert abs(min(pen.y) - 11.6) < 0.5 and abs(max(pen.y) - 50.5) < 0.5

    assert hl.tool.family is ir.ToolFamily.HIGHLIGHTER
    assert "HIGHLIGHT_STROKES" in hl.extra["nebo"]["tags"]
    assert hl.extra["nebo"]["brush"] == "brush-0500"
    assert hl.appearance.width == 5.0        # brush-0500 -> 5 mm
    assert hl.appearance.underlay is True
    assert abs(hl.appearance.opacity - 0x66 / 255) < 1e-6
    assert hl.color == ir.Color(1.0, 0xDD / 255, 0x33 / 255)  # #FFDD33
    # svg path bbox (75.19, 5.03)-(167.97, 61.95) inflated by ~2.5mm
    assert abs(min(hl.x) - 77.9) < 0.5 and abs(max(hl.x) - 165.3) < 0.5
    assert abs(min(hl.y) - 5.9) < 0.5 and abs(max(hl.y) - 61.2) < 0.5


def test_registry_picks_nebo():
    from inkterop.formats import reader_for
    assert type(reader_for(FIXTURE)).__name__ == "NeboReader"
