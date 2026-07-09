"""Supernote .note reader: detection, raster-first ingestion, structure.

Fixtures are synthetic X-series files built by
tests/fixtures/supernote/make_fixture.py (see the README there) and
verified to parse with supernotelib's strict policy.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from inkterop import ir
from inkterop.formats.supernote import SupernoteReader

FIXTURES = Path(__file__).parent / "fixtures" / "supernote"
TWO_PAGE = FIXTURES / "synthetic-two-page.note"
LANDSCAPE = FIXTURES / "synthetic-landscape.note"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_detect_accepts_fixtures():
    reader = SupernoteReader()
    assert reader.detect(TWO_PAGE)
    assert reader.detect(LANDSCAPE)


def test_detect_rejects_foreign_files(tmp_path):
    reader = SupernoteReader()
    # Notability also names its zip archives .note — must not match.
    notability = tmp_path / "notability.note"
    with zipfile.ZipFile(notability, "w") as z:
        z.writestr("Session.plist", b"not ink")
    assert not reader.detect(notability)

    rm = (Path(__file__).parent / "fixtures" / "remarkable"
          / "fineliner-pencil-colors.rm")
    assert not reader.detect(rm)

    assert not reader.detect(tmp_path / "missing.note")
    empty = tmp_path / "empty.note"
    empty.write_bytes(b"")
    assert not reader.detect(empty)


def test_read_two_page_document():
    doc = SupernoteReader().read(TWO_PAGE)
    doc.validate()

    assert doc.format_id == "supernote"
    assert doc.title == "synthetic-two-page"
    assert doc.orientation == "portrait"
    assert doc.metadata["signature"].startswith("SN_FILE_VER_")
    assert doc.metadata["device_pixels"] == [1404, 1872]
    assert len(doc.pages) == 2

    for page in doc.pages:
        assert page.bounds.width == pytest.approx(1404.0)
        assert page.bounds.height == pytest.approx(1872.0)
        assert page.point_scale == pytest.approx(595.0 / 1404.0)
        # full page maps to ~letter-sized points
        assert page.bounds.height * page.point_scale == pytest.approx(793.3, abs=0.5)
        assert page.layers, "expected raster layer(s)"
        for layer in page.layers:
            assert not layer.strokes  # raster-first: no vector strokes
            assert layer.raster is not None
            assert layer.raster.format == "png"
            assert layer.raster.data[:8] == PNG_MAGIC
            assert layer.visible

    layer = doc.pages[0].layers[0]
    assert layer.name == "MAINLAYER"
    assert doc.pages[0].extra["supernote"]["style"] == "style_white"


def test_raster_pixels_match_source_geometry():
    """The isolated-layer PNG has ink where the fixture drew it and a
    transparent background elsewhere."""
    PIL = pytest.importorskip("PIL.Image")
    doc = SupernoteReader().read(TWO_PAGE)
    img = PIL.open(io.BytesIO(doc.pages[0].layers[0].raster.data))
    assert img.size == (1404, 1872)
    assert img.mode == "RGBA"
    px = img.load()
    assert px[500, 600] == (0, 0, 0, 255)  # inside the black rect
    assert px[700, 1050][3] == 255  # gray band is opaque
    assert px[700, 1050][0] > 150  # ...and gray, not black
    assert px[10, 10][3] == 0  # background transparent


def test_landscape_orientation():
    doc = SupernoteReader().read(LANDSCAPE)
    doc.validate()
    assert doc.orientation == "landscape"
    page = doc.pages[0]
    assert page.bounds.width == pytest.approx(1872.0)
    assert page.bounds.height == pytest.approx(1404.0)
    assert page.layers[0].raster is not None


def test_pdf_conversion_smoke(tmp_path):
    """Structure-only smoke through the PDF writer path.

    render/pdf.py does not draw Layer.raster yet, so raster-first pages
    come out blank; we only assert the pipeline accepts the document and
    emits a well-formed PDF.
    """
    from inkterop.render.pdf import render_document

    doc = SupernoteReader().read(TWO_PAGE)
    doc.validate()
    assert all(layer.raster for page in doc.pages for layer in page.layers)

    out = tmp_path / "supernote.pdf"
    render_document(doc, out)
    assert out.read_bytes()[:5] == b"%PDF-"
