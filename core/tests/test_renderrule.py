"""Measured rendering rules: forward/inverse consistency and known values.

Constants under test were fitted against official app exports; the
measurements live in docs/calibration-results.md. These tests pin the
code to the recorded laws — a failure means the constants drifted from
what was measured, not that the measurement is wrong.
"""
from __future__ import annotations

import math

import pytest

from inkterop import ir
from inkterop.ir import renderrule as rr


def test_registry_entries():
    assert set(rr.RULES) >= {"excalidraw", "remarkable", "nebo"}
    for entry in rr.RULES.values():
        assert entry.evidence  # every rule cites its oracle


def test_excalidraw_known_values():
    # 8.08x at p=1.0, 6.01x at p=0.5 (docs/formats/excalidraw.md)
    assert rr.excalidraw_thickness_factor(1.0) == pytest.approx(8.08, abs=0.01)
    assert rr.excalidraw_thickness_factor(0.5) == pytest.approx(6.01, abs=0.01)


def test_excalidraw_inverse_roundtrip():
    for p in (0.0, 0.25, 0.5, 0.75, 1.0):
        ratio = rr.excalidraw_thickness_factor(p)
        assert rr.excalidraw_pressure_for_ratio(ratio) == pytest.approx(p, abs=1e-9)


def test_calligraphy_direction_law():
    # Width peaks when the stroke travels along the nib-normal direction
    # and bottoms out 90 degrees away; pressure adds on top.
    peak = rr.remarkable_calligraphy_width(rr.RM_CALLIG_NIB_RAD, 0.5)
    trough = rr.remarkable_calligraphy_width(rr.RM_CALLIG_NIB_RAD + math.pi / 2, 0.5)
    assert peak > trough
    # pi-periodic in direction (a nib has no front/back)
    assert rr.remarkable_calligraphy_width(0.3, 0.5) == pytest.approx(
        rr.remarkable_calligraphy_width(0.3 + math.pi, 0.5))
    # thickness_scale is proportional
    assert rr.remarkable_calligraphy_width(0.3, 0.5, 2.0) == pytest.approx(
        2.0 * rr.remarkable_calligraphy_width(0.3, 0.5, 1.0))
    # pressure increases width
    assert (rr.remarkable_calligraphy_width(0.3, 1.0)
            > rr.remarkable_calligraphy_width(0.3, 0.0))


def test_nebo_forward_law():
    # sensitivity 0 renders the base width regardless of force
    assert rr.nebo_rendered_width(0.0, 5.0, 0.0) == pytest.approx(5.0)
    assert rr.nebo_rendered_width(1.0, 5.0, None) == pytest.approx(5.0)
    # at the pivot force the pen renders its base width
    assert rr.nebo_rendered_width(rr.NEBO_FORCE_PIVOT, 0.25, 0.8) == \
        pytest.approx(0.25)
    # measured span at s=0.8: ~0.44x base at f=0, ~2.4x at f=1
    assert rr.nebo_rendered_width(0.0, 0.25, 0.8) / 0.25 == \
        pytest.approx(0.44, abs=0.02)
    assert rr.nebo_rendered_width(1.0, 0.25, 0.8) / 0.25 == \
        pytest.approx(2.38, abs=0.02)
    # never collapses to zero
    assert rr.nebo_rendered_width(0.0, 0.25, 5.0) >= 0.2 * 0.25


def test_nebo_inverse_roundtrip():
    for f in (0.1, 0.29, 0.5, 0.9):
        w = rr.nebo_rendered_width(f, 0.25, 0.8)
        assert rr.nebo_force_for_width(w, 0.25, 0.8) == pytest.approx(f, abs=1e-9)
    # degenerate cases pin to the pivot
    assert rr.nebo_force_for_width(1.0, 0.25, 0.0) == rr.NEBO_FORCE_PIVOT


def test_nebo_reader_bakes_width():
    """Varying force -> WIDTH channel via the rule; constant force -> not."""
    from inkterop.formats.nebo.reader import _ir_stroke

    raw = {"x": [0.0, 1.0, 2.0], "y": [0.0, 0.0, 0.0],
           "f": [50, 128, 250], "t0_us": 0}
    s = _ir_stroke(raw, [], {}, "pen-025")
    assert s.appearance.mode is ir.GeometryMode.STROKED_VARIABLE
    assert s.appearance.width is None
    widths = s.channels[ir.Channel.WIDTH]
    assert widths == [rr.nebo_rendered_width(f / 255.0, 0.25, 0.8)
                      for f in raw["f"]]
    assert widths[0] < widths[1] < widths[2]

    flat = _ir_stroke({**raw, "f": [255, 255, 255]}, [], {}, "pen-025")
    assert flat.appearance.mode is ir.GeometryMode.STROKED_CONSTANT
    assert flat.appearance.width == 0.25
    assert ir.Channel.WIDTH not in flat.channels

    hl = _ir_stroke(raw, ["HIGHLIGHT_STROKES"], {"pressure_sensitivity": 0.0},
                    "brush-0500")
    assert hl.appearance.mode is ir.GeometryMode.STROKED_CONSTANT
    assert hl.appearance.width == 5.0
