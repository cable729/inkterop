"""Appearance: how the SOURCE app renders a stroke.

Populated by readers from observed/reverse-engineered rendering behavior
(e.g. reMarkable's official export draws highlighters as full-opacity
30-unit strokes with /BM /Darken). `fidelity=exact` output consumes this;
`fidelity=native` ignores it and restyles from the semantic tool.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class GeometryMode(str, Enum):
    STROKED_CONSTANT = "stroked_constant"  # one polyline, one width
    STROKED_VARIABLE = "stroked_variable"  # polyline, per-point WIDTH channel
    FILLED_OUTLINE = "filled_outline"  # variable width drawn as filled polygon


class BlendMode(str, Enum):
    NORMAL = "normal"
    DARKEN = "darken"
    MULTIPLY = "multiply"


class LineCap(str, Enum):
    ROUND = "round"
    SQUARE = "square"
    BUTT = "butt"


@dataclass
class Color:
    """RGBA, all components 0.0-1.0."""

    r: float
    g: float
    b: float
    a: float = 1.0

    def rgb(self) -> tuple[float, float, float]:
        return (self.r, self.g, self.b)


@dataclass
class StrokeAppearance:
    mode: GeometryMode
    color: Color  # resolved render color (may differ from semantic color)
    width: float | None = None  # constant width, source units; None => WIDTH channel
    opacity: float = 1.0  # stroke-level; per-point ALPHA channel wins if present
    blend: BlendMode = BlendMode.NORMAL
    cap: LineCap = LineCap.ROUND
    join: LineCap = LineCap.ROUND
    underlay: bool = False  # draw beneath ordinary ink (rM highlighter trick)
