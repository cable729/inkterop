"""Default (semantic) stroke appearance per tool family.

Used for `fidelity=native` output and for strokes whose reader supplied no
appearance: the target restyles from the neutral tool family alone.
"""
from __future__ import annotations

from .channels import Channel
from .model import Stroke
from .style import BlendMode, Color, GeometryMode, LineCap, StrokeAppearance
from .tools import ToolFamily

FALLBACK_WIDTH = 2.0  # source units, when no WIDTH channel exists


def default_appearance(stroke: Stroke) -> StrokeAppearance:
    fam = stroke.tool.family
    widths = stroke.channels.get(Channel.WIDTH)
    w0 = widths[0] if widths else FALLBACK_WIDTH

    if fam is ToolFamily.HIGHLIGHTER:
        return StrokeAppearance(
            mode=GeometryMode.STROKED_CONSTANT, width=w0, color=stroke.color,
            opacity=0.85, blend=BlendMode.DARKEN, cap=LineCap.SQUARE,
            underlay=True,
        )
    if fam is ToolFamily.SHADER:
        return StrokeAppearance(
            mode=GeometryMode.STROKED_VARIABLE, color=stroke.color,
            opacity=0.45, blend=BlendMode.DARKEN, cap=LineCap.SQUARE,
            underlay=True,
        )
    if fam is ToolFamily.FINELINER:
        return StrokeAppearance(
            mode=GeometryMode.STROKED_CONSTANT, width=w0, color=stroke.color,
        )
    if fam is ToolFamily.ERASER:
        return StrokeAppearance(
            mode=GeometryMode.STROKED_VARIABLE, color=Color(1.0, 1.0, 1.0),
        )
    return StrokeAppearance(
        mode=GeometryMode.STROKED_VARIABLE, color=stroke.color,
    )


def restyled(stroke: Stroke) -> Stroke:
    """Copy of the stroke with appearance rebuilt from its tool family."""
    return Stroke(
        x=stroke.x, y=stroke.y, tool=stroke.tool, color=stroke.color,
        channels=stroke.channels, appearance=default_appearance(stroke),
        extra={k: v for k, v in stroke.extra.items() if k != "inkterop"},
    )
