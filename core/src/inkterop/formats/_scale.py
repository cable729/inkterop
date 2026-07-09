"""Unit-conversion helper shared by writers.

IR coordinates stay in source units; each page carries ``point_scale``
(source units -> PDF points). A writer targeting its own unit system with
``TARGET_SCALE`` (target units -> points) converts lengths by
``unit_factor(page, TARGET_SCALE)`` and rebases coordinates to the page's
top-left corner.
"""
from __future__ import annotations

from .. import ir


def unit_factor(page: ir.Page, target_scale: float) -> float:
    """Multiply source-unit lengths by this to get target units."""
    return page.point_scale / target_scale
