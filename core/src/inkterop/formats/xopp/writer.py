"""IR -> Xournal++ .xopp.

Open format, so this writer ships validated by round-trip tests rather
than app-open checks; Xournal++ is also expected to open the output (its
parser is lenient). Coordinates are rebased so the page's top-left is
(0,0) and scaled to points via page.point_scale.
"""
from __future__ import annotations

import gzip
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from ... import ir
from ..base import Fidelity
from .common import FORMAT_ID, KIND_TO_STYLE, color_to_hex, family_to_tool


def _fmt(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def _stroke_xml(s: ir.Stroke, scale: float, x0: float, y0: float,
                fidelity: Fidelity) -> str:
    app = s.appearance if fidelity is not Fidelity.NATIVE else None
    tool = family_to_tool(s.tool.family)
    color = app.color if app else s.color
    opacity = app.opacity if app else (
        0.5 if tool == "highlighter" else 1.0
    )

    xs, ys = list(s.x), list(s.y)
    widths_ch = s.channels.get(ir.Channel.WIDTH)
    if len(xs) == 1:
        # Xournal++ rejects strokes with < 2 points ("Wrong count of
        # points"); emit dots as a minimal segment instead.
        xs.append(xs[0] + 0.001)
        ys.append(ys[0])
        if widths_ch:
            widths_ch = [widths_ch[0], widths_ch[0]]

    constant = app is not None and app.mode is ir.GeometryMode.STROKED_CONSTANT
    if constant:
        width_attr = _fmt(app.width * scale)
    elif widths_ch and len(widths_ch) > 1:
        vals = [widths_ch[0]] + widths_ch[1:]  # base + per-segment widths
        width_attr = " ".join(_fmt(w * scale) for w in vals)
    else:
        width_attr = _fmt((widths_ch[0] if widths_ch else 2.0) * scale)

    coords = " ".join(
        f"{_fmt((x - x0) * scale)} {_fmt((y - y0) * scale)}"
        for x, y in zip(xs, ys)
    )
    return (f'<stroke tool="{tool}" color="{color_to_hex(color, opacity)}" '
            f'width="{width_attr}">{coords}</stroke>')


def _text_xml(t: ir.TextBlock, scale: float, x0: float, y0: float) -> str:
    color = color_to_hex(t.color or ir.Color(0, 0, 0))
    size = t.font_size or 12.0
    return (f'<text font="Sans" size="{_fmt(size)}" '
            f'x="{_fmt((t.x - x0) * scale)}" y="{_fmt((t.y - y0) * scale)}" '
            f'color="{color}">{escape(t.text)}</text>')


def document_to_xml(doc: ir.Document, fidelity: Fidelity = Fidelity.EXACT) -> str:
    out = ['<?xml version="1.0" standalone="no"?>',
           '<xournal creator="inkterop" fileversion="4">',
           f"<title>{escape(doc.title or 'inkterop export')}</title>"]
    for page in doc.pages:
        scale = page.point_scale
        b = page.bounds
        w, h = b.width * scale, b.height * scale
        style = "plain"
        if isinstance(page.background, ir.TemplateBackground):
            style = KIND_TO_STYLE.get(page.background.kind, "plain")
        out.append(f'<page width="{_fmt(w)}" height="{_fmt(h)}">')
        out.append(f'<background type="solid" color="#ffffffff" style={quoteattr(style)}/>')
        layers = page.layers or [ir.Layer()]
        for layer in layers:
            out.append("<layer>")
            for s in layer.strokes:
                if len(s.x) >= 1:
                    out.append(_stroke_xml(s, scale, b.x_min, b.y_min, fidelity))
            for t in layer.texts:
                out.append(_text_xml(t, scale, b.x_min, b.y_min))
            out.append("</layer>")
        out.append("</page>")
    out.append("</xournal>")
    return "\n".join(out) + "\n"


class XoppWriter:
    format_id = FORMAT_ID
    extensions = (".xopp",)
    validated = True  # open format; round-trip covered in tests

    def write(self, doc: ir.Document, path: Path, fidelity: Fidelity,
              options: dict[str, Any] | None = None) -> None:
        if fidelity is Fidelity.RAW:
            raise ValueError(
                "xopp cannot hold raw pen dynamics (pressure/tilt/speed); "
                "use .json (IR) or InkML"
            )
        xml = document_to_xml(doc, fidelity)
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(xml)
