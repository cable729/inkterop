"""Neutral tool taxonomy + lossless native-tool carry-through.

`ToolFamily` is what a *foreign* writer consumes ("this is a highlighter,
use my highlighter"). `NativeTool` preserves the source format's exact tool
identity so a *same-format* writer can round-trip perfectly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolFamily(str, Enum):
    PEN = "pen"
    BALLPOINT = "ballpoint"
    FINELINER = "fineliner"
    PENCIL = "pencil"
    MECHANICAL_PENCIL = "mechanical_pencil"
    MARKER = "marker"
    HIGHLIGHTER = "highlighter"
    SHADER = "shader"
    BRUSH = "brush"
    CALLIGRAPHY = "calligraphy"
    ERASER = "eraser"
    UNKNOWN = "unknown"


@dataclass
class NativeTool:
    """The source format's own tool record, untouched."""

    format_id: str  # e.g. "remarkable"
    tool_id: str | int  # raw enum value / tool name in the source format
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolRef:
    family: ToolFamily
    native: NativeTool | None = None
