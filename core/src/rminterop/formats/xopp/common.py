"""Shared xopp <-> IR mapping tables.

Format reference: gzipped XML, fileversion 4 (Xournal `.xoj` extension;
see xournalpp source and issue #2124). Coordinates are PDF points, y down,
origin top-left. A <stroke>'s `width` attribute is a space-separated list:
first the nominal width, then (for pressure strokes) one width per SEGMENT
(n-1 values for n points).
"""
from __future__ import annotations

from ... import ir

FORMAT_ID = "xopp"

# xournal background style <-> IR template kind
STYLE_TO_KIND = {
    "dotted": "dots",
    "lined": "lines",
    "ruled": "lines",
    "graph": "grid",
    "plain": "",
}
KIND_TO_STYLE = {
    "dots": "dotted",
    "lines": "ruled",
    "grid": "graph",
}

TOOL_TO_FAMILY = {
    "pen": ir.ToolFamily.PEN,
    "highlighter": ir.ToolFamily.HIGHLIGHTER,
    "eraser": ir.ToolFamily.ERASER,
}


def family_to_tool(family: ir.ToolFamily) -> str:
    if family in (ir.ToolFamily.HIGHLIGHTER, ir.ToolFamily.SHADER):
        return "highlighter"
    if family is ir.ToolFamily.ERASER:
        return "eraser"
    return "pen"


def color_to_hex(c: ir.Color, opacity: float = 1.0) -> str:
    a = c.a * opacity
    return "#{:02x}{:02x}{:02x}{:02x}".format(
        round(c.r * 255), round(c.g * 255), round(c.b * 255), round(a * 255)
    )


def hex_to_color(s: str) -> ir.Color:
    s = s.lstrip("#")
    if len(s) == 6:
        s += "ff"
    r, g, b, a = (int(s[i:i + 2], 16) / 255 for i in (0, 2, 4, 6))
    return ir.Color(r, g, b, a)


# Named colors Xournal++ may emit instead of hex.
NAMED_COLORS = {
    "black": ir.Color(0, 0, 0),
    "blue": ir.Color(0.2, 0.2, 0.8),
    "red": ir.Color(1, 0, 0),
    "green": ir.Color(0, 0.5, 0),
    "gray": ir.Color(0.5, 0.5, 0.5),
    "lightblue": ir.Color(0, 0.75, 1),
    "lightgreen": ir.Color(0, 1, 0),
    "magenta": ir.Color(1, 0, 1),
    "orange": ir.Color(1, 0.65, 0),
    "yellow": ir.Color(1, 1, 0),
    "white": ir.Color(1, 1, 1),
}


def parse_color(s: str) -> ir.Color:
    if s.startswith("#"):
        return hex_to_color(s)
    return NAMED_COLORS.get(s, ir.Color(0, 0, 0))
