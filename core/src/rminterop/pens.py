"""Per-pen stroke calibration.

Width/opacity formulas adapted from rmc (MIT, github.com/ricklupton/rmc,
originally lschwetlick/maxio via chemag/maxio). Colors returned as
(r, g, b, alpha) floats 0-1.

Two styles:
- "faithful": ink stays solid (device-like); width still responds to
  pressure/speed. Fixes the washed-out gray look of exported Ballpoint.
- "rmc": upstream behavior, intensity fades with pressure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

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
    PenColor.GREEN_2: (161, 216, 125),
    PenColor.CYAN: (139, 208, 229),
    PenColor.MAGENTA: (183, 130, 205),
    PenColor.YELLOW_2: (247, 232, 81),
}

HIGHLIGHT_ALPHA = 0.35


def _clamp(v: float) -> float:
    return min(max(v, 0.0), 1.0)


def base_rgb(color: PenColor, color_rgba: tuple | None) -> tuple[float, float, float]:
    if color_rgba is not None:
        r, g, b = color_rgba[0], color_rgba[1], color_rgba[2]
        return (r / 255, g / 255, b / 255)
    rgb = RM_PALETTE.get(color, RM_PALETTE[PenColor.BLACK])
    return (rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)


@dataclass
class Segment:
    width: float  # screen units
    rgb: tuple[float, float, float]
    alpha: float
    cap: str  # "round" | "square"


class PenModel:
    """Maps rmscene per-point data to drawable segment properties."""

    segment_length = 1000  # points per segment before re-evaluating
    cap = "round"

    def __init__(self, tool: PenType, color: PenColor, color_rgba, thickness: float, style: str):
        self.tool = tool
        self.rgb = base_rgb(color, color_rgba)
        self.thickness = thickness
        self.style = style

    @staticmethod
    def tilt(direction: float) -> float:
        return direction * (math.pi * 2) / 255

    def segment(self, speed, direction, width, pressure) -> Segment:
        return Segment(self.width(speed, direction, width, pressure),
                       self.color(speed, direction, width, pressure),
                       self.alpha(speed, direction, width, pressure),
                       self.cap)

    def width(self, speed, direction, width, pressure) -> float:
        return self.thickness

    def color(self, speed, direction, width, pressure):
        return self.rgb

    def alpha(self, speed, direction, width, pressure) -> float:
        return 1.0

    @classmethod
    def create(cls, tool: PenType, color: PenColor, color_rgba,
               thickness: float, style: str = "faithful") -> "PenModel":
        klass = {
            PenType.BALLPOINT_1: Ballpoint, PenType.BALLPOINT_2: Ballpoint,
            PenType.FINELINER_1: Fineliner, PenType.FINELINER_2: Fineliner,
            PenType.MARKER_1: Marker, PenType.MARKER_2: Marker,
            PenType.PENCIL_1: Pencil, PenType.PENCIL_2: Pencil,
            PenType.MECHANICAL_PENCIL_1: MechanicalPencil,
            PenType.MECHANICAL_PENCIL_2: MechanicalPencil,
            PenType.PAINTBRUSH_1: Brush, PenType.PAINTBRUSH_2: Brush,
            PenType.CALIGRAPHY: Calligraphy,
            PenType.HIGHLIGHTER_1: Highlighter, PenType.HIGHLIGHTER_2: Highlighter,
            PenType.SHADER: Shader,
            PenType.ERASER: Eraser, PenType.ERASER_AREA: EraseArea,
        }.get(tool, PenModel)
        return klass(tool, color, color_rgba, thickness, style)


class Ballpoint(PenModel):
    segment_length = 5

    def width(self, speed, direction, width, pressure):
        w = (0.5 + pressure / 255) + (width / 4) - 0.5 * ((speed / 4) / 50)
        if self.style == "faithful":
            # Device ballpoint never gets hairline-thin; floor the width.
            w = max(w, 0.75 * self.thickness)
        return w

    def color(self, speed, direction, width, pressure):
        if self.style == "faithful":
            return self.rgb  # solid ink, like on-device rendering
        intensity = _clamp((0.1 * -((speed / 4) / 35)) + (1.2 * pressure / 255) + 0.5)
        v = min(abs(intensity - 1), 60 / 255)
        return (v, v, v) if self.rgb == (0, 0, 0) else self.rgb


class Fineliner(PenModel):
    def __init__(self, *args):
        super().__init__(*args)
        self.thickness *= 1.8


class Marker(PenModel):
    segment_length = 3

    def width(self, speed, direction, width, pressure):
        return 0.9 * ((width / 4) - 0.4 * self.tilt(direction))


class Pencil(PenModel):
    segment_length = 2

    def width(self, speed, direction, width, pressure):
        w = 0.7 * ((((0.8 * self.thickness) + (0.5 * pressure / 255)) * (width / 4))
                   - (0.25 * self.tilt(direction) ** 1.8) - (0.6 * (speed / 4) / 50))
        return min(w, self.thickness * 10)

    def alpha(self, speed, direction, width, pressure):
        return max(_clamp((0.1 * -((speed / 4) / 35)) + pressure / 255) - 0.1, 0.05)


class MechanicalPencil(PenModel):
    def __init__(self, *args):
        super().__init__(*args)
        self.thickness = self.thickness ** 2

    def alpha(self, speed, direction, width, pressure):
        return 0.7


class Brush(PenModel):
    segment_length = 2

    def width(self, speed, direction, width, pressure):
        return 0.7 * (((1 + (1.4 * pressure / 255)) * (width / 4))
                      - (0.5 * self.tilt(direction)) - ((speed / 4) / 50))

    def color(self, speed, direction, width, pressure):
        intensity = _clamp(((pressure / 255) ** 1.5 - 0.2 * ((speed / 4) / 50)) * 1.5)
        rev = abs(intensity - 1)
        r, g, b = self.rgb
        return (r + rev * (1 - r), g + rev * (1 - g), b + rev * (1 - b))


class Calligraphy(PenModel):
    segment_length = 2

    def width(self, speed, direction, width, pressure):
        return 0.9 * (((1 + pressure / 255) * (width / 4)) - 0.3 * self.tilt(direction))


class Highlighter(PenModel):
    cap = "square"

    def __init__(self, *args):
        super().__init__(*args)
        self.thickness = 15

    def alpha(self, speed, direction, width, pressure):
        return HIGHLIGHT_ALPHA


class Shader(PenModel):
    def __init__(self, *args):
        super().__init__(*args)
        self.thickness = 12

    def alpha(self, speed, direction, width, pressure):
        return 0.1


class Eraser(PenModel):
    cap = "square"

    def __init__(self, *args):
        super().__init__(*args)
        self.thickness *= 2
        self.rgb = (1, 1, 1)


class EraseArea(PenModel):
    def alpha(self, speed, direction, width, pressure):
        return 0.0
