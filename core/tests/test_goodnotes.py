"""GoodNotes reader: wire-format units + public-corpus integration.

Corpus tests use third-party samples from the gitignored corpus/ dir
(study material, never redistributed) and skip when absent. Self-generated
committable fixtures arrive with the Mac-app corpus (docs/corpus-protocol.md).
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from inkterop import ir
from inkterop.formats.goodnotes import GoodnotesReader
from inkterop.formats.goodnotes.reader import extract_path, parse_tpl
from inkterop.formats.goodnotes.wire import (
    apple_lz4_decompress,
    fields_by_number,
    lz4_block_decompress,
    parse_message,
    split_delimited,
)

CORPUS = (Path(__file__).parents[2] / "corpus" / "third-party"
          / "goodparse" / "samples")

needs_corpus = pytest.mark.skipif(
    not CORPUS.exists(), reason="third-party corpus not present"
)


# --- wire units (always run) -------------------------------------------------

def test_parse_message_scalar_types():
    #  field 1 varint 150; field 2 fixed32 1.5; field 3 bytes "hi"
    buf = b"\x08\x96\x01" + b"\x15" + struct.pack("<f", 1.5) + b"\x1a\x02hi"
    fields = fields_by_number(buf)
    assert fields[1][0].value == 150
    assert fields[2][0].value == pytest.approx(1.5)
    assert fields[3][0].value == b"hi"


def test_split_delimited():
    msg = b"\x08\x01"
    stream = bytes([len(msg)]) + msg + bytes([len(msg)]) + msg
    assert split_delimited(stream) == [msg, msg]


def test_lz4_block_round_trip():
    # Hand-built block: 5 literals "AAAAA" then a match copying 8 bytes
    # from offset 5 (overlapping run), then trailing literals "BC".
    #   token 0x54: lit_len 5, match_len 4+4=8; offset 5
    block = b"\x54AAAAA\x05\x00" + b"\x20BC"
    out = lz4_block_decompress(block, 15)
    assert out == b"AAAAA" + b"AAAAAAAA" + b"BC"


def test_apple_lz4_raw_block():
    framed = b"bv4-" + struct.pack("<I", 3) + b"xyz" + b"bv4$"
    assert apple_lz4_decompress(framed) == b"xyz"


def _tpl_blob(triplets, sig=b"vA(u)"):
    body = struct.pack("<H", 2)
    floats = [v for t in triplets for v in t]
    body += struct.pack("<I", len(floats)) + struct.pack(f"<{len(floats)}f",
                                                         *floats)
    blob = b"tpl\x00" + b"????" + sig + b"\x00" + body
    return blob[:4] + struct.pack("<I", len(blob)) + blob[8:]


def test_parse_tpl_and_extract_path():
    pts = [(10.0, 20.0, 1.5), (30.0, 40.0, 2.0), (50.0, 60.0, 1.0)]
    blob = _tpl_blob(pts)
    sections = parse_tpl(blob)
    assert sections[0] == ("scalar", "v", [2])
    assert sections[1][:2] == ("array", "u")
    path, constant = extract_path(blob)
    assert path == [pytest.approx(t) for t in pts]
    assert constant is False


def test_extract_path_rejects_singleton_section():
    blob = _tpl_blob([(10.0, 20.0, 1.5)])  # 3 floats = 1 triplet: anchor only
    assert extract_path(blob) == ([], False)


def _tpl_struct_blob(pairs, width, sig=b"vuA(S(uu))"):
    body = struct.pack("<H", 2) + struct.pack("<f", width)
    body += struct.pack("<I", len(pairs))
    for x, y in pairs:
        body += struct.pack("<2f", x, y)
    blob = b"tpl\x00" + b"????" + sig + b"\x00" + body
    return blob[:4] + struct.pack("<I", len(blob)) + blob[8:]


def test_extract_path_constant_width_struct_layout():
    """Fresh-export layout: width scalar + A(S(uu)) point pairs."""
    pairs = [(10.0, 20.0), (30.0, 40.0), (50.0, 60.0)]
    path, constant = extract_path(_tpl_struct_blob(pairs, 24.0))
    assert constant is True
    assert path == [pytest.approx((x, y, 24.0)) for x, y in pairs]


def test_extract_path_tilt_sample_pairs():
    """iPad pressure strokes: 9-float (x1,y1,w1, x2,y2,w2, alt1,alt2,k)
    sample pairs, selected by bit 2 of the flags section. Must NOT be
    read as flat triplets (divisible by 3 too — the old triplet branch
    produced phantom points from the tilt columns)."""
    from inkterop.formats.goodnotes.wire import encode_tpl

    groups = [
        (510.3, 38.8, 0.39, 508.8, 38.6, 0.55, 1.73, 1.71, 0.6),
        (507.8, 38.4, 0.70, 508.4, 39.3, 0.73, 1.70, 1.69, 0.6),
        (509.0, 40.1, 0.75, 510.2, 41.0, 0.71, 1.68, 1.68, 0.6),
    ]
    blob = encode_tpl([
        ("scalar", "v", [2]),
        ("array", "v", [4, 5, 5]),                    # bit 2 = tilt layout
        ("array", "u", [510.3, 38.8, 0.39, 1.75]),    # anchor
        ("array", "u", [v for g in groups for v in g]),
    ])
    path, constant = extract_path(blob)
    assert constant is False
    assert len(path) == 6  # two samples per group
    assert all(507 < x < 511 for x, _, _ in path)  # no phantom tilt points
    assert path[0] == pytest.approx((510.3, 38.8, 0.39), abs=1e-3)
    assert path[1] == pytest.approx((508.8, 38.6, 0.55), abs=1e-3)


def test_parse_tpl_rejects_residue():
    blob = _tpl_blob([(10.0, 20.0, 1.5), (30.0, 40.0, 2.0)])
    with pytest.raises(Exception, match="tpl"):
        parse_tpl(blob + b"junk!")


# --- self-generated fixture (always runs) ------------------------------------

FIXTURE = Path(__file__).parent / "fixtures" / "goodnotes" / \
    "gn-mac-mixed-pens.goodnotes"


def test_fixture_mixed_pens():
    """GoodNotes 6 Mac export (schema 25): all layout variants in one page.

    Draw order on the fixture page: fountain, ball, brush, pencil,
    highlighter, marker, shape ellipse (no inline ink). Pen style comes
    from stroke fields 3/5/20 — stroke field 7 is an identity index, not
    a pen type (2026-07-10 calibration-page finding).
    """
    reader = GoodnotesReader()
    assert reader.detect(FIXTURE)
    doc = reader.read(FIXTURE)
    doc.validate()
    strokes = list(doc.pages[0].strokes())
    assert len(strokes) == 6  # 5 pens + highlighter; shape ellipse skipped
    by_style: dict[str, list] = {}
    for s in strokes:
        by_style.setdefault(s.tool.native.tool_id, []).append(s)
    assert {k: len(v) for k, v in by_style.items()} == {
        "pressure": 2,  # fountain + brush (not distinguishable per stroke)
        "ball": 1, "pencil": 1, "highlighter": 1, "marker": 1,
    }
    assert {s.tool.family for s in by_style["pressure"]} == {ir.ToolFamily.PEN}
    assert by_style["ball"][0].tool.family is ir.ToolFamily.BALLPOINT
    assert by_style["pencil"][0].tool.family is ir.ToolFamily.PENCIL
    hl = by_style["highlighter"][0]
    assert hl.tool.family is ir.ToolFamily.HIGHLIGHTER
    assert hl.appearance.width == pytest.approx(24.0)
    assert hl.appearance.underlay is True
    # red marker stroke (field 20 = {1: ""}), 9-float sample pairs
    marker = by_style["marker"][0]
    assert marker.tool.family is ir.ToolFamily.MARKER
    assert marker.color.r > 0.9 and marker.color.g < 0.5
    assert min(marker.x) > 500  # right-hand region, not origin
    assert all(w == pytest.approx(18.0)
               for w in marker.channels[ir.Channel.WIDTH])
    assert all(0 < w <= 60
               for s in strokes for w in s.channels[ir.Channel.WIDTH])


# --- corpus integration ------------------------------------------------------

@needs_corpus
def test_detect_and_read_samples():
    reader = GoodnotesReader()
    sample = CORPUS / "Test4.goodnotes"
    assert reader.detect(sample)
    doc = reader.read(sample)
    doc.validate()
    assert len(doc.pages) >= 1
    strokes = [s for p in doc.pages for s in p.strokes()]
    assert strokes, "expected ink in Test4.goodnotes"
    for s in strokes:
        widths = s.channels[ir.Channel.WIDTH]
        assert len(widths) == len(s.x)
        assert all(0 < w < 60 for w in widths)
        assert all(0 <= x <= 2000 for x in s.x)
        assert all(0 <= y <= 2000 for y in s.y)
    colors = {s.color.rgb() for s in strokes}
    assert len(colors) >= 2, "Test4 has multiple pen colors"


@needs_corpus
def test_does_not_detect_other_formats():
    reader = GoodnotesReader()
    rm = Path(__file__).parent / "fixtures" / "remarkable" / "ballpoint-small.rm"
    assert not reader.detect(rm)


@needs_corpus
def test_sample_to_pdf(tmp_path):
    from inkterop.convert import convert

    out = tmp_path / "gn.pdf"
    convert(CORPUS / "Test4.goodnotes", out)
    assert out.read_bytes()[:5] == b"%PDF-"
