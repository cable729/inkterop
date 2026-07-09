"""Notability readers: legacy .note (synthetic + corpus) and modern .ntb."""
from __future__ import annotations

import plistlib
import struct
import zipfile
from pathlib import Path

import pytest

from inkterop import ir
from inkterop.formats.notability import NotabilityReader, NtbReader
from inkterop.formats.notability.ntb import decode_point_blob
from inkterop.formats.notability.reader import read_session

NTB_FIXTURE = Path(__file__).parent / "fixtures" / "notability" / "scribbles.ntb"

CORPUS = (Path(__file__).parents[2] / "corpus" / "third-party"
          / "notability-reader-sample")

needs_corpus = pytest.mark.skipif(
    not CORPUS.exists(), reason="third-party corpus not present"
)


def synthetic_session(curves) -> bytes:
    """Build a minimal GLKeyedArchiver-shaped Session.plist."""
    points = b"".join(struct.pack("<2f", x, y)
                      for xs, ys, *_ in curves for x, y in zip(xs, ys))
    numpoints = b"".join(struct.pack("<i", len(xs)) for xs, *_ in curves)
    widths = b"".join(struct.pack("<f", w) for _, _, w, _ in curves)
    colors = b"".join(bytes(c) for *_, c in curves)
    hw = {
        "$class": plistlib.UID(3),
        "curvespoints": plistlib.UID(4),
        "curvesnumpoints": plistlib.UID(5),
        "curveswidth": plistlib.UID(6),
        "curvescolors": plistlib.UID(7),
        "numcurves": len(curves),
        "numpoints": sum(len(xs) for xs, *_ in curves),
    }
    archive = {
        "$version": 100000,
        "$archiver": "GLKeyedArchiver",
        "$top": {"$0": plistlib.UID(1)},
        "$objects": [
            "$null",
            {"$class": plistlib.UID(2)},
            {"$classname": "NoteTakingSession", "$classes": []},
            {"$classname": "HandwritingObject", "$classes": []},
            points, numpoints, widths, colors,
        ],
    }
    archive["$objects"][1] = hw
    return plistlib.dumps(archive, fmt=plistlib.FMT_BINARY)


CURVES = [
    ([10.0, 20.0, 30.0], [5.0, 6.0, 7.0], 2.5, (255, 0, 0, 255)),
    ([0.0, 100.0], [50.0, 50.0], 11.0, (250, 157, 0, 68)),
]


def test_read_session_synthetic():
    doc = read_session(synthetic_session(CURVES))
    doc.validate()
    strokes = list(doc.pages[0].strokes())
    assert len(strokes) == 2

    pen, marker = strokes
    assert pen.x == pytest.approx([10.0, 20.0, 30.0])
    assert pen.y == pytest.approx([5.0, 6.0, 7.0])
    assert pen.appearance.width == pytest.approx(2.5)
    assert pen.color.rgb() == pytest.approx((1.0, 0.0, 0.0))
    assert pen.tool.family is ir.ToolFamily.PEN

    assert marker.tool.family is ir.ToolFamily.HIGHLIGHTER
    assert marker.appearance.opacity == pytest.approx(68 / 255)
    assert marker.appearance.underlay is True


def test_read_session_rejects_non_archive():
    with pytest.raises(ValueError, match="NSKeyedArchiver"):
        read_session(plistlib.dumps(["just", "a", "list"],
                                    fmt=plistlib.FMT_BINARY))


def test_detect_zip_shapes(tmp_path):
    reader = NotabilityReader()
    note = tmp_path / "doc.note"
    with zipfile.ZipFile(note, "w") as zf:
        zf.writestr("MyNote/Session.plist", synthetic_session(CURVES))
        zf.writestr("MyNote/metadata.plist", b"")
    assert reader.detect(note)
    doc = reader.read(note)
    assert doc.title == "doc"
    assert len(list(doc.pages[0].strokes())) == 2

    other = tmp_path / "other.note"
    with zipfile.ZipFile(other, "w") as zf:
        zf.writestr("whatever.txt", b"nope")
    assert not reader.detect(other)

    rm = Path(__file__).parent / "fixtures" / "remarkable" / "ballpoint-small.rm"
    assert not reader.detect(rm)


# --- modern .ntb (FlatBuffers noteBundle) -----------------------------------
#
# scribbles.ntb is self-generated (CC0): Mac app 16.5.3, one fountain-pen
# scribble (black), two pencil scribbles (black), one highlighter zigzag
# (yellow, alpha 107). Ground truth in docs/formats/notability.md.


def test_ntb_detect_and_read():
    reader = NtbReader()
    assert reader.detect(NTB_FIXTURE)
    doc = reader.read(NTB_FIXTURE)
    doc.validate()

    assert doc.title == "Note Jul 9, 2026"
    assert doc.metadata["app_version"] == "16.5.3"
    assert doc.metadata["notability_uuid"] == (
        "224c68e0-22c8-4f97-a542-ca9cd8d7d469")

    page = doc.pages[0]
    assert (page.bounds.width, page.bounds.height) == (612.0, 792.0)

    strokes = list(page.strokes())
    assert [s.tool.family for s in strokes] == [
        ir.ToolFamily.PEN, ir.ToolFamily.PENCIL,
        ir.ToolFamily.HIGHLIGHTER, ir.ToolFamily.PENCIL,
    ]


def test_ntb_stroke_geometry():
    doc = NtbReader().read(NTB_FIXTURE)
    pen, pencil1, marker, pencil2 = doc.pages[0].strokes()

    # Anchors per stroke (declared point counts): 94, 136, 173, 91;
    # flattened at 4 samples/segment => (n-1)*4 + 1 points.
    assert [len(s) for s in (pen, pencil1, marker, pencil2)] == [
        373, 541, 689, 361]

    # First point == the stored f32 origin.
    assert (pen.x[0], pen.y[0]) == pytest.approx((78.684, 160.639), abs=1e-3)

    # Extents validated against the app's own thumbnail render.
    assert min(pen.x) == pytest.approx(58.4, abs=0.5)
    assert max(pen.x) == pytest.approx(286.9, abs=0.5)
    assert min(pen.y) == pytest.approx(74.4, abs=0.5)
    assert max(pen.y) == pytest.approx(328.0, abs=0.5)

    # All ink inside the page (highlighter grazes the top edge).
    for s in (pen, pencil1, marker, pencil2):
        assert min(s.x) > 0 and max(s.x) < 612
        assert max(s.y) < 792

    # Fountain pen carries a decaying width profile; base width 3.1875.
    pw = pen.channels[ir.Channel.WIDTH]
    assert pw[0] == pytest.approx(3.1875 * 1.2725, rel=1e-3)
    assert pw[-1] == pytest.approx(3.1875 * 0.3767, rel=1e-3)
    # Pencils/highlighter: constant multiplier 1.0.
    assert set(pencil1.channels[ir.Channel.WIDTH]) == {3.1875}
    assert set(marker.channels[ir.Channel.WIDTH]) == {15.9375}

    # Highlighter styling.
    assert marker.color.rgb() == pytest.approx((1.0, 1.0, 0.0))
    assert marker.appearance.opacity == pytest.approx(107 / 255)
    assert marker.appearance.underlay is True


def test_ntb_point_blob_framing_errors():
    with pytest.raises(ValueError, match="coordinate format"):
        decode_point_blob(bytes([7, 1, 0, 3, 0, 0, 0, 0]))
    # Declared 3 points but zero segment records.
    bad = struct.pack("<BHB4x", 0, 3, 3) + struct.pack("<eeBB", 1.0, 1.0, 255, 0)
    with pytest.raises(ValueError, match="segments"):
        decode_point_blob(bad)


def test_ntb_not_confused_with_legacy(tmp_path):
    # NtbReader must not claim legacy zips, nor NotabilityReader claim .ntb.
    legacy = tmp_path / "old.note"
    with zipfile.ZipFile(legacy, "w") as zf:
        zf.writestr("MyNote/Session.plist", synthetic_session(CURVES))
    assert not NtbReader().detect(legacy)
    assert not NotabilityReader().detect(NTB_FIXTURE)


@needs_corpus
def test_corpus_session():
    data = (CORPUS / "Session.plist").read_bytes()
    doc = read_session(data)
    doc.validate()
    strokes = list(doc.pages[0].strokes())
    assert len(strokes) == 294  # numcurves in the sample
    assert sum(len(s) for s in strokes) == 18099  # numpoints
    # Highlighter strokes (alpha < 0.5) exist in this sample.
    assert any(s.tool.family is ir.ToolFamily.HIGHLIGHTER for s in strokes)
