"""Resolve xochitl template names into concrete IR background params.

Approximates the built-in dots/lines/grid templates (real art lives at
/usr/share/remarkable/templates/*.svg on-device; exact art is a later
milestone). Constants match the validated legacy renderer exactly.
"""
from __future__ import annotations

from ... import ir

TEMPLATE_GRAY = 0.62
DOT_SPACING = 39.0  # canvas units; approximates "Dots S" pitch
DOT_RADIUS = 1.7
LINE_SPACING = {"S": 55.0, "M": 78.0, "L": 110.0}
LINE_WIDTH = 0.6


def resolve_template(name: str) -> ir.TemplateBackground | None:
    """Template name from .content -> background params (None = blank)."""
    if not name or name == "Blank":
        return None
    if "Dots" in name:
        return ir.TemplateBackground(
            kind="dots", name=name, pitch=DOT_SPACING,
            dot_radius=DOT_RADIUS, gray=TEMPLATE_GRAY,
        )
    if "Grid" in name or "Lines" in name:
        size = name.rsplit(" ", 1)[-1] if name and name[-1] in "SML" else "M"
        return ir.TemplateBackground(
            kind="grid" if "Grid" in name else "lines",
            name=name, pitch=LINE_SPACING.get(size, 78.0),
            line_width=LINE_WIDTH, gray=TEMPLATE_GRAY,
        )
    return ir.TemplateBackground(kind="unknown", name=name)
