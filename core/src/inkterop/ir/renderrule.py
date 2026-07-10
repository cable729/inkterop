"""Measured per-app rendering rules: stored stroke data -> on-canvas look.

Each app renders its stored channels (pressure/force/tilt) through its own
engine; moving strokes between apps only looks right when that rule is
known. A *forward* rule maps native stroke data to the rendered per-point
width, used by readers to bake resolved WIDTH into the IR; an *inverse*
rule encodes a desired rendered width back into native parameters, used
by writers (the Excalidraw pattern: synthetic pressures reproduce a
target width profile).

Every constant here was fitted against the app's own official export —
never a community formula. The measurement corpus and per-tool tables
live in docs/calibration-results.md; method in docs/calibration-pages.md.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class RuleEntry:
    """Registry record: what is measured for a format, and how well."""

    format_id: str
    summary: str
    evidence: str  # oracle + date; confidence markers per docs convention


# ---------------------------------------------------------------------------
# Excalidraw — perfect-freehand freedraw thickness [verified]
# Oracle: @excalidraw/excalidraw 0.18.0 exportToSvg (docs/formats/excalidraw.md)
# ---------------------------------------------------------------------------

EXCALIDRAW_FREEDRAW_SIZE = 8.5
EXCALIDRAW_FREEDRAW_THINNING = 0.6


def excalidraw_thickness_factor(pressure: float) -> float:
    """Rendered freedraw thickness per unit strokeWidth at a pressure."""
    t = 0.5 + EXCALIDRAW_FREEDRAW_THINNING * (pressure - 0.5)
    return EXCALIDRAW_FREEDRAW_SIZE * math.sin(math.pi / 2.0 * t)


def excalidraw_pressure_for_ratio(ratio: float) -> float:
    """Inverse of excalidraw_thickness_factor: ratio = thickness/strokeWidth."""
    t = 2.0 / math.pi * math.asin(
        max(0.0, min(1.0, ratio / EXCALIDRAW_FREEDRAW_SIZE)))
    return max(0.0, min(1.0, 0.5 + (t - 0.5) / EXCALIDRAW_FREEDRAW_THINNING))


# ---------------------------------------------------------------------------
# reMarkable — WIDTH channel IS the rendered width [verified]
# Oracle: desktop 3.27.2 SVG export of the calibration page (2026-07-10).
# Constant-width tools (fineliner, highlighter) export stroke-width equal to
# the stored channel exactly; ballpoint/calligraphy/shader outline ribbons
# measure 1.01-1.02x the channel. Soft-edge tools (marker/brush ~0.8x,
# pencil ~0.6x) export outlines at an opacity threshold inside the nominal
# width — the channel still drives the geometry. Readers need no forward
# rule; the device already folds pressure/speed/direction into the channel.
# ---------------------------------------------------------------------------

# Calligraphy synthesis (the inverse rule, for writers targeting rM):
# the device computes calligraphy width from STROKE DIRECTION against a
# fixed nib axis, plus pressure — NOT from pen tilt (tilt_azimuth refuted,
# calibration round 1). Fit R2=0.54 on 313 points, single thickness_scale
# (2.0) in sample, so the ts proportionality is [inferred].
RM_CALLIG_NIB_RAD = math.radians(92.0)  # width peaks at this stroke direction
RM_CALLIG_BASE = 2.855
RM_CALLIG_DIR_COEF = -2.176
RM_CALLIG_PRESSURE_COEF = 1.004


def remarkable_calligraphy_width(direction_rad: float, pressure: float,
                                 thickness_scale: float = 1.0) -> float:
    """Synthesize a device-plausible calligraphy WIDTH (display units).

    direction_rad: local stroke travel direction, atan2(dy, dx) in display
    coordinates. pressure: 0-1.
    """
    w = (RM_CALLIG_BASE
         + RM_CALLIG_DIR_COEF * abs(math.sin(direction_rad - RM_CALLIG_NIB_RAD))
         + RM_CALLIG_PRESSURE_COEF * pressure)
    return thickness_scale * max(w, 0.5)


# ---------------------------------------------------------------------------
# Nebo / MyScript — rendered width from force x pressure-sensitivity
# Oracle: Nebo iPad 7.4.3 SVG export of the calibration page (2026-07-10).
# Pens store a constant base width (brush name, e.g. pen-025 = 0.25 mm) and
# a per-point force channel; the app renders width varying linearly with
# force. Measured at sensitivity 0.8 (pens): rendered/base spans 0.33-2.44,
# crossing 1.0 at force ~0.29. Highlighter (sensitivity 0.0) renders
# constant 1.06x base — consistent with the same law at s=0, so the slope
# is parametrized by sensitivity [inferred: only s=0.8 and s=0 sampled].
# ---------------------------------------------------------------------------

NEBO_FORCE_PIVOT = 0.29
NEBO_SENSITIVITY_SLOPE = 2.43  # (rendered/base) per (sensitivity x force)
NEBO_MIN_WIDTH_FACTOR = 0.2


def nebo_rendered_width(force: float, base_width: float,
                        sensitivity: float | None) -> float:
    """Forward rule: on-canvas width (base_width units, i.e. mm) at a force."""
    s = sensitivity or 0.0
    w = base_width * (1.0 + s * NEBO_SENSITIVITY_SLOPE
                      * (force - NEBO_FORCE_PIVOT))
    return max(w, NEBO_MIN_WIDTH_FACTOR * base_width)


def nebo_force_for_width(width: float, base_width: float,
                         sensitivity: float | None) -> float:
    """Inverse rule: force (0-1) that renders the given width."""
    s = sensitivity or 0.0
    if s <= 0.0 or base_width <= 0.0:
        return NEBO_FORCE_PIVOT
    f = NEBO_FORCE_PIVOT + (width / base_width - 1.0) / (s * NEBO_SENSITIVITY_SLOPE)
    return max(0.0, min(1.0, f))


RULES: dict[str, RuleEntry] = {
    "excalidraw": RuleEntry(
        "excalidraw",
        "freedraw thickness = strokeWidth x 8.5 x sin(pi/2 x (0.5 + 0.6(p-0.5)))",
        "[verified] @excalidraw/excalidraw 0.18.0 exportToSvg; "
        "docs/formats/excalidraw.md",
    ),
    "remarkable": RuleEntry(
        "remarkable",
        "rendered width = stored WIDTH channel (device pre-computes; "
        "calligraphy width is driven by stroke direction + pressure, not tilt)",
        "[verified] desktop 3.27.2 SVG export vs calibration page, "
        "2026-07-10; docs/calibration-results.md",
    ),
    "nebo": RuleEntry(
        "nebo",
        "rendered width = base x (1 + sensitivity x 2.43 x (force - 0.29))",
        "[verified at s=0.8 and s=0] Nebo iPad 7.4.3 SVG export vs "
        "calibration page, 2026-07-10; docs/calibration-results.md",
    ),
}
