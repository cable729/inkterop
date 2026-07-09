"""PencilKit PKDrawing reader tests against oracle truth JSONs.

The *.truth.json fixtures are PencilKit's own readback of each drawing
(see fixtures/pkdrawing/README.md): quantization happens at
PKStrokePoint construction, so decoded values must match EXACTLY (up to
float noise), not approximately.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from inkterop import ir
from inkterop.formats.base import Fidelity
from inkterop.formats.pencilkit import (
    FORMAT_ID,
    MAGIC,
    PkDrawingError,
    PkDrawingReader,
    parse_pkdrawing,
)

FIXDIR = Path(__file__).parent / "fixtures" / "pkdrawing"
TWO_PI = 2.0 * math.pi

CASES = ["case01-dot", "case04-pressure-ramp", "case06-inks"]


def _read(name: str) -> ir.Document:
    return PkDrawingReader().read(FIXDIR / f"{name}.pkdrawing")


def _truth(name: str) -> dict:
    return json.loads((FIXDIR / f"{name}.truth.json").read_text())


def _per_point(doc_stroke: ir.Stroke, channel: ir.Channel, i: int,
               default: float) -> float:
    values = doc_stroke.channels.get(channel)
    return values[i] if values else default


# ---------------------------------------------------------------- detect

def test_detect_fixtures():
    reader = PkDrawingReader()
    for name in ["case00-empty", *CASES]:
        assert reader.detect(FIXDIR / f"{name}.pkdrawing"), name


def test_detect_rejects_other_formats():
    reader = PkDrawingReader()
    fixtures = Path(__file__).parent / "fixtures"
    for other in [
        fixtures / "remarkable" / "ballpoint-small.rm",
        fixtures / "isf" / "pen-pressure-tilt.isf",
        fixtures / "uim" / "two-strokes-pressure.uim",
        fixtures / "pkdrawing" / "case01-dot.truth.json",
    ]:
        assert not reader.detect(other), other.name
    assert not reader.detect(fixtures / "pkdrawing" / "does-not-exist")


def test_parse_rejects_bad_magic():
    with pytest.raises(PkDrawingError, match="magic"):
        parse_pkdrawing(b"garbage bytes")
    with pytest.raises(PkDrawingError):
        parse_pkdrawing(MAGIC)  # magic but truncated header


# ---------------------------------------------------------------- decode

def test_empty_drawing():
    doc = _read("case00-empty")
    doc.validate()
    assert doc.metadata["pk_container_version"] == 1
    assert doc.metadata["ink_ids"] == []
    assert len(doc.pages) == 1
    assert list(doc.pages[0].strokes()) == []
    assert doc.pages[0].bounds.width > 0  # fallback page size


@pytest.mark.parametrize("name", CASES)
def test_truth_oracle(name):
    """Every decoded control point matches PencilKit's readback exactly."""
    doc = _read(name)
    doc.validate()
    truth = _truth(name)
    strokes = list(doc.pages[0].strokes())
    assert len(strokes) == truth["strokeCount"]

    for s, ts in zip(strokes, truth["strokes"]):
        assert s.tool.native.format_id == FORMAT_ID
        assert s.tool.native.tool_id == ts["ink"]
        c = ts["color"]
        assert (s.color.r, s.color.g, s.color.b, s.color.a) == \
            pytest.approx((c["r"], c["g"], c["b"], c["a"]))
        assert len(s) == ts["controlPointCount"]

        widths = s.channels[ir.Channel.WIDTH]
        times = s.channels[ir.Channel.TIMESTAMP]
        pressures = s.channels[ir.Channel.PRESSURE]
        azimuths = s.channels[ir.Channel.TILT_AZIMUTH]
        altitudes = s.channels[ir.Channel.TILT_ALTITUDE]
        aspect_pp = s.extra[FORMAT_ID].get("aspect")
        sec_pp = s.extra[FORMAT_ID].get("secondary_scale")

        for i, tp in enumerate(ts["controlPoints"]):
            assert s.x[i] == pytest.approx(tp["x"], abs=1e-9)
            assert s.y[i] == pytest.approx(tp["y"], abs=1e-9)
            assert times[i] == pytest.approx(tp["t"], abs=1e-12)
            assert widths[i] == pytest.approx(tp["w"], abs=1e-9)
            assert pressures[i] == pytest.approx(tp["force"], abs=1e-12)
            # reader normalizes -pi..pi -> [0, 2pi): same angle mod 2pi
            assert azimuths[i] % TWO_PI == \
                pytest.approx(tp["azimuth"] % TWO_PI, abs=1e-12)
            assert altitudes[i] == pytest.approx(tp["altitude"], abs=1e-12)
            opacity = _per_point(s, ir.Channel.ALPHA, i,
                                 s.appearance.opacity)
            assert opacity == pytest.approx(tp["opacity"], abs=1e-12)
            aspect = (aspect_pp[i] if aspect_pp
                      else s.tool.native.params["aspect"])
            assert widths[i] * aspect == pytest.approx(tp["h"], abs=1e-6)
            sec = (sec_pp[i] if sec_pp
                   else s.tool.native.params["secondary_scale"])
            assert sec == pytest.approx(tp["secondaryScale"], abs=1e-9)

        rb = ts["renderBounds"]
        assert s.extra[FORMAT_ID]["render_bounds"] == \
            pytest.approx([rb["x"], rb["y"], rb["w"], rb["h"]])


def test_dot_all_constant_channels():
    """1-point stroke: only location is per-point; everything else rides
    the constant block (masks 0x001 / 0x7FE) and still comes back as
    length-1 channels."""
    doc = _read("case01-dot")
    (s,) = doc.pages[0].strokes()
    assert len(s) == 1
    for ch in (ir.Channel.WIDTH, ir.Channel.TIMESTAMP, ir.Channel.PRESSURE,
               ir.Channel.TILT_AZIMUTH, ir.Channel.TILT_ALTITUDE):
        assert len(s.channels[ch]) == 1
    assert (s.x[0], s.y[0]) == (100.0, 100.0)
    assert s.channels[ir.Channel.PRESSURE] == [0.5]
    assert s.channels[ir.Channel.TILT_ALTITUDE][0] == \
        pytest.approx(math.pi / 2)
    # opacity constant -> stroke-level appearance, no ALPHA channel
    assert ir.Channel.ALPHA not in s.channels
    assert s.appearance.opacity == pytest.approx(2 * 32767 / 65535)
    assert s.appearance.mode is ir.GeometryMode.STROKED_VARIABLE
    assert s.appearance.width is None


def test_pressure_ramp_per_point():
    doc = _read("case04-pressure-ramp")
    (s,) = doc.pages[0].strokes()
    pressures = s.channels[ir.Channel.PRESSURE]
    assert len(pressures) == 9
    assert pressures[0] == pytest.approx(0.1)
    assert pressures == sorted(pressures)  # a ramp
    assert all(0.0 <= p <= 1.0 for p in pressures)
    times = s.channels[ir.Channel.TIMESTAMP]
    assert times == sorted(times) and times[-1] > 0
    # no clamping happened -> no raw stash
    assert "force_raw" not in s.extra[FORMAT_ID]


def test_multi_ink_families_and_colors():
    doc = _read("case06-inks")
    strokes = list(doc.pages[0].strokes())
    assert [s.tool.family for s in strokes] == [
        ir.ToolFamily.PEN, ir.ToolFamily.PENCIL, ir.ToolFamily.MARKER]
    assert doc.metadata["ink_ids"] == [
        "com.apple.ink.pen", "com.apple.ink.pencil", "com.apple.ink.marker"]
    marker = strokes[2]
    assert (marker.color.r, marker.color.g, marker.color.b) == (1.0, 1.0, 0.0)
    # marker mapping decision: normal blend, no underlay [inferred]
    assert marker.appearance.blend is ir.BlendMode.NORMAL
    assert marker.appearance.underlay is False


def test_bounds_are_padded_renderbounds_union():
    doc = _read("case06-inks")
    truth = _truth("case06-inks")
    rbs = [s["renderBounds"] for s in truth["strokes"]]
    b = doc.pages[0].bounds
    assert b.x_min == pytest.approx(min(r["x"] for r in rbs) - 10)
    assert b.y_min == pytest.approx(min(r["y"] for r in rbs) - 10)
    assert b.x_max == pytest.approx(max(r["x"] + r["w"] for r in rbs) + 10)
    assert b.y_max == pytest.approx(max(r["y"] + r["h"] for r in rbs) + 10)
    assert doc.pages[0].point_scale == 1.0
    # all points inside the page bounds
    for s in doc.pages[0].strokes():
        assert all(b.x_min <= x <= b.x_max for x in s.x)
        assert all(b.y_min <= y <= b.y_max for y in s.y)


def test_pressure_clamped_with_raw_stash():
    """Synthetic blob check via a real one: patch case01's constant force
    (0.5 -> 2.5) and confirm the clamp + raw stash."""
    blob = bytearray((FIXDIR / "case01-dot.pkdrawing").read_bytes())
    needle = (500).to_bytes(2, "little")  # force u16 = 0.5 * 1000
    idx = blob.find(needle)
    assert idx != -1
    blob[idx:idx + 2] = (2500).to_bytes(2, "little")
    doc = parse_pkdrawing(bytes(blob))
    (s,) = doc.pages[0].strokes()
    assert s.channels[ir.Channel.PRESSURE] == [1.0]
    assert s.extra[FORMAT_ID]["force_raw"] == [2.5]


def test_to_pdf_smoke(tmp_path):
    from inkterop.render.pdf import PdfWriter

    for name in CASES:
        out = tmp_path / f"{name}.pdf"
        PdfWriter().write(_read(name), out, Fidelity.EXACT)
        assert out.read_bytes()[:5] == b"%PDF-"
