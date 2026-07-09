"""Per-pen stroke styling.

Ground truth (reverse-engineered from an official Paper Pro export):
- The v6 file stores a device-computed rendered width per point; the official
  export draws strokes at exactly point.width/4 canvas units. Variable-width
  pens (ballpoint, calligraphy, brush...) are drawn as filled outlines;
  constant-width pens as stroked polylines.
- Highlighter: full opacity, /Darken blend, width from points, color from the
  stroke's color_rgba. We approximate Darken by drawing highlights beneath ink.

"faithful" style (default) follows that model. "rmc" style keeps the
community formulas from rmc (MIT, github.com/ricklupton/rmc) for comparison.
"""

from __future__ import annotations

import math

from rmscene.scene_items import Pen as PenType
from rmscene.scene_items import PenColor

RM_PALETTE = {
    PenColor.BLACK: (0, 0, 0),
    PenColor.GRAY: (144, 144, 144),
    PenColor.WHITE: (255, 255, 255),
    PenColor.YELLOW: (251, 247, 25),
    PenColor.GREEN: (0, 255, 0),
    PenColor.PINK: (255, 192, 203),
    PenColor.BLUE: (78, 105, 201),
    PenColor.RED: (179, 62, 57),
    PenColor.GRAY_OVERLAP: (125, 125, 125),
    PenColor.HIGHLIGHT: (255, 237, 117),
    PenColor.GREEN_2: (161, 216, 125),
    PenColor.CYAN: (139, 208, 229),
    PenColor.MAGENTA: (183, 130, 205),
    PenColor.YELLOW_2: (247, 232, 81),
}

HIGHLIGHT_TOOLS = {PenType.HIGHLIGHTER_1, PenType.HIGHLIGHTER_2, PenType.SHADER}
# Pens whose stored per-point width is constant -> single stroked polyline.
CONSTANT_WIDTH_TOOLS = {PenType.FINELINER_1, PenType.FINELINER_2,
                        PenType.HIGHLIGHTER_1, PenType.HIGHLIGHTER_2}


def _clamp(v: float) -> float:
    return min(max(v, 0.0), 1.0)


def base_rgb(color: PenColor, color_rgba) -> tuple[float, float, float]:
    if color_rgba is not None:
        return (color_rgba[0] / 255, color_rgba[1] / 255, color_rgba[2] / 255)
    rgb = RM_PALETTE.get(color, RM_PALETTE[PenColor.BLACK])
    return (rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)


class PenModel:
    """Maps rmscene per-point data to width (canvas units) / color / alpha."""

    def __init__(self, tool: PenType, color: PenColor, color_rgba,
                 thickness: float, style: str = "faithful"):
        self.tool = tool
        self.rgb = base_rgb(color, color_rgba)
        self.thickness = thickness
        self.style = style
        self.is_highlight = tool in HIGHLIGHT_TOOLS
        self.constant_width = tool in CONSTANT_WIDTH_TOOLS
        self.cap = "square" if self.is_highlight else "round"

    def width(self, p) -> float:
        """Stroke width in canvas units at point p."""
        if self.style == "faithful" or self.is_highlight:
            return max(p.width / 4.0, 0.5)
        return self._rmc_width(p)

    def alpha(self, p) -> float:
        if self.is_highlight:
            # Drawn beneath ink (see render order); shader is lighter wash.
            return 0.85 if self.tool != PenType.SHADER else 0.45
        if self.tool in (PenType.PENCIL_1, PenType.PENCIL_2):
            # Pencil is textured on-device; grain reads as partial opacity.
            return max(_clamp(p.pressure / 255) - 0.05, 0.25)
        if self.tool in (PenType.MECHANICAL_PENCIL_1, PenType.MECHANICAL_PENCIL_2):
            return 0.8
        if self.tool == PenType.ERASER_AREA:
            return 0.0
        return 1.0

    def color(self, p) -> tuple[float, float, float]:
        if self.tool == PenType.ERASER:
            return (1.0, 1.0, 1.0)
        if self.style == "rmc" and self.tool in (PenType.BALLPOINT_1, PenType.BALLPOINT_2):
            intensity = _clamp(0.1 * -(p.speed / 140) + 1.2 * p.pressure / 255 + 0.5)
            v = min(abs(intensity - 1), 60 / 255)
            return (v, v, v) if self.rgb == (0.0, 0.0, 0.0) else self.rgb
        return self.rgb

    # --- legacy rmc width formulas (style="rmc") ---
    def _rmc_width(self, p) -> float:
        t, w, pr, sp = self.tool, p.width / 4, p.pressure / 255, p.speed / 4
        tilt = p.direction * (math.pi * 2) / 255
        if t in (PenType.BALLPOINT_1, PenType.BALLPOINT_2):
            return (0.5 + pr) + w - 0.5 * (sp / 50)
        if t in (PenType.MARKER_1, PenType.MARKER_2):
            return 0.9 * (w - 0.4 * tilt)
        if t in (PenType.PENCIL_1, PenType.PENCIL_2):
            return min(0.7 * (((0.8 * self.thickness) + 0.5 * pr) * w
                              - 0.25 * tilt ** 1.8 - 0.6 * sp / 50),
                       self.thickness * 10)
        if t in (PenType.PAINTBRUSH_1, PenType.PAINTBRUSH_2):
            return 0.7 * ((1 + 1.4 * pr) * w - 0.5 * tilt - sp / 50)
        if t == PenType.CALIGRAPHY:
            return 0.9 * ((1 + pr) * w - 0.3 * tilt)
        if t in (PenType.FINELINER_1, PenType.FINELINER_2):
            return self.thickness * 1.8
        return max(p.width / 4.0, 0.5)
