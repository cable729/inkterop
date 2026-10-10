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


def native_session(curves=CURVES, *, media=None, layout=None, paper="letter",
                   orientation="portrait", width=567.0):
    archive = plistlib.loads(synthetic_session(curves))
    objs = archive["$objects"]
    objs.append({
        "richText": {
            "reflowState": {"pageWidthInDocumentCoordsKey": width},
            "mediaObjects": {"NS.objects": media or []},
        },
        "NBNoteTakingSessionDocumentPaperLayoutModelKey": {
            "documentPaperAttributes": {"paperSize": paper, "paperOrientation": orientation},
            "pageLayoutArray": {"NS.objects": layout or []},
        },
    })
    archive["$top"] = {"root": plistlib.UID(len(objs) - 1)}
    return plistlib.dumps(archive, fmt=plistlib.FMT_BINARY)


def image_object(origin="{10, 100}", size="{40, 20}", path="Images/picture.png",
                 crop="{{0, 0}, {4, 2}}", **kwargs):
    return {"documentOrigin": origin, "unscaledContentSize": size,
            "figure": {"FigureCropRectKey": crop,
                       "FigureBackgroundObjectKey": {
                           "kImageObjectSnapshotKey": {"relativePath": path}}},
            **kwargs}


def tiny_png():
    from PIL import Image
    from io import BytesIO
    image = Image.new("RGB", (4, 2), "red")
    image.putpixel((3, 0), (0, 0, 255))
    stream = BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


def test_native_pages_use_paper_geometry_not_ink_bounds():
    height = 744.1875
    curves = [([10, 20], [20, 25], 2, (255, 0, 0, 255)),
              ([30, 35], [height + 50, height + 60], 3, (0, 0, 255, 255))]
    doc = read_session(native_session(curves))
    assert len(doc.pages) == 2
    assert [p.bounds.height for p in doc.pages] == [height, height]
    assert [p.bounds.width for p in doc.pages] == [567, 567]
    assert list(doc.pages[1].strokes())[0].y == [height + 50, height + 60]
    # The stored layout can be shorter than the content, or include trailing blanks.
    doc = read_session(native_session(curves, layout=[{"kPageLayoutDocumentPageNumberKey": 4}]))
    assert len(doc.pages) == 4
    assert not list(doc.pages[3].strokes())
    doc = read_session(native_session(curves, layout=[{"kPageLayoutDocumentPageNumberKey": 1}]))
    assert len(doc.pages) == 2


@pytest.mark.parametrize("paper,orientation,w,h", [
    ("letter", "portrait", 612, 803.25),
    ("letter", "landscape", 803.25, 612),
    ("a4", "portrait", 595.28, 841.89),
])
def test_native_paper_dimensions(paper, orientation, w, h):
    doc = read_session(native_session(paper=paper, orientation=orientation, width=w))
    assert doc.pages[0].bounds.height == pytest.approx(h)
    assert doc.orientation == orientation


def test_cross_page_stroke_is_split_with_interpolated_channels():
    height = 744.1875
    curves = [([10, 30], [height - 10, height + 10], 2.5, (255, 0, 0, 107))]
    doc = read_session(native_session(curves))
    doc.validate()
    a, b = [list(p.strokes())[0] for p in doc.pages]
    assert a.x == [10, 20] and b.x == [20, 30]
    assert a.y == [height - 10, height] and b.y == [height, height + 10]
    assert a.channels[ir.Channel.WIDTH] == b.channels[ir.Channel.WIDTH] == [2.5, 2.5]
    assert a.appearance.opacity == b.appearance.opacity == 107 / 255
    # Touching a boundary doesn't make an extra blank page; dots on it do.
    doc = read_session(native_session([([10, 20], [20, height], 2, (0, 0, 0, 255))]))
    assert len(doc.pages) == 1
    doc = read_session(native_session([([10], [height], 2, (0, 0, 0, 255))]))
    assert len(doc.pages) == 2
    assert len(list(doc.pages[1].strokes())[0]) == 1


def test_stroke_crosses_multiple_pages_and_returns():
    doc = read_session(native_session([([10, 30, 50], [10, 2300, 20], 2, (0, 0, 0, 255))]))
    doc.validate()
    assert len(doc.pages) == 4
    assert [len(list(p.strokes())) for p in doc.pages] == [2, 2, 2, 1]
    for page in doc.pages:
        for stroke in page.strokes():
            assert min(stroke.y) >= page.bounds.y_min - 1e-9
            assert max(stroke.y) <= page.bounds.y_max + 1e-9


def test_image_asset_geometry_and_crop_flip():
    from PIL import Image
    from io import BytesIO
    data = tiny_png()
    doc = read_session(native_session(media=[image_object()]), lambda _: data)
    raster = doc.pages[0].layers[0].raster
    assert raster.data == data  # intact PNG/JPEGs stay byte-identical
    assert raster.bounds == ir.Rect(10, 100, 50, 120)
    obj = image_object(crop="{{2, 0}, {2, 2}}", isFlippedHorizontal=True)
    doc = read_session(native_session(media=[obj]), lambda _: data)
    img = Image.open(BytesIO(doc.pages[0].layers[0].raster.data))
    assert img.size == (2, 2) and img.getpixel((0, 0)) == (0, 0, 255)


def test_image_crossing_page_and_image_only_note():
    doc = read_session(native_session([], media=[image_object(
        origin="{10, 740}", size="{40, 20}")]), lambda _: tiny_png())
    assert len(doc.pages) == 2
    rasters = [next(l.raster for l in p.layers if l.raster) for p in doc.pages]
    assert rasters[0].bounds == ir.Rect(10, 740, 50, 744.1875)
    assert rasters[1].bounds == ir.Rect(10, 744.1875, 50, 760)
    assert sum(r.bounds.height for r in rasters) == 20


def test_rotated_image_keeps_display_center():
    from PIL import Image
    from io import BytesIO
    obj = image_object(rotationDegrees=90)
    doc = read_session(native_session([], media=[obj]), lambda _: tiny_png())
    image = doc.pages[0].layers[0].raster
    assert image.bounds == ir.Rect(20, 90, 40, 130)
    assert Image.open(BytesIO(image.data)).size == (2, 4)


def test_pdf_backed_layout_is_not_misread_as_native_pages():
    data = native_session(layout=[{"kPageLayoutPDFFileNameKey": "source.pdf"}])
    with pytest.raises(ValueError, match="PDF-backed"):
        read_session(data)


def test_complete_archive_reads_only_referenced_images(tmp_path):
    note = tmp_path / "example.note"
    with zipfile.ZipFile(note, "w") as z:
        z.writestr("Example/Session.plist", native_session(media=[image_object()]))
        z.writestr("Example/Images/picture.png", tiny_png())
        z.writestr("Example/Images/deleted.png", b"unused stale asset")
    doc = NotabilityReader().read(note)
    assert sum(l.raster is not None for p in doc.pages for l in p.layers) == 1


@pytest.mark.parametrize("path,match", [("../outside.png", "unsafe"),
                                        ("Images/missing.png", "missing")])
def test_missing_or_unsafe_image_is_not_silently_dropped(tmp_path, path, match):
    note = tmp_path / "bad.note"
    with zipfile.ZipFile(note, "w") as z:
        z.writestr("Example/Session.plist", native_session(media=[image_object(path=path)]))
    with pytest.raises(ValueError, match=match):
        NotabilityReader().read(note)


def test_numbered_keyed_archive_containers():
    from inkterop.formats.notability.legacy_layout import resolve
    objects = [None, {"NS.key.0": "a", "NS.object.0": plistlib.UID(2)},
               {"NS.object.0": "first", "NS.object.1": "second"}]
    assert resolve(objects, plistlib.UID(1)) == {"a": ["first", "second"]}
    objects += [{"NS.bytes": b"\xff\x00", "$class": plistlib.UID(4)},
                {"$classname": "NSData"},
                {"NS.bytes": b"Images/a.png", "$class": plistlib.UID(6)},
                {"$classname": "NSMutableString"}]
    assert resolve(objects, plistlib.UID(3)) == b"\xff\x00"
    assert resolve(objects, plistlib.UID(5)) == "Images/a.png"


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
