"""Xournal++ .xopp -> IR.

Accepts gzipped (normal) or plain XML. Strokes become IR strokes with a
WIDTH channel (nominal width + per-segment widths expanded back to
per-point) and an appearance derived from the tool + color alpha.
"""
from __future__ import annotations

import gzip
import logging
from pathlib import Path
from xml.etree import ElementTree

from ... import ir
from .common import FORMAT_ID, STYLE_TO_KIND, TOOL_TO_FAMILY, parse_color

_logger = logging.getLogger(__name__)


def _read_xml(path: Path) -> ElementTree.Element:
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return ElementTree.fromstring(raw)


def _stroke_from_xml(el: ElementTree.Element) -> ir.Stroke | None:
    coords = [float(v) for v in (el.text or "").split()]
    if len(coords) < 2:
        return None
    xs = coords[0::2]
    ys = coords[1::2]
    n = len(xs)

    tool = el.get("tool", "pen")
    family = TOOL_TO_FAMILY.get(tool, ir.ToolFamily.PEN)
    color = parse_color(el.get("color", "#000000ff"))
    opacity, color = color.a, ir.Color(color.r, color.g, color.b)

    width_vals = [float(v) for v in el.get("width", "2").split()]
    if len(width_vals) >= n:  # nominal + per-segment widths
        widths = [width_vals[0]] + width_vals[1:n]
        constant = False
    else:
        widths = [width_vals[0]] * n
        constant = True

    appearance = ir.StrokeAppearance(
        mode=(ir.GeometryMode.STROKED_CONSTANT if constant
              else ir.GeometryMode.STROKED_VARIABLE),
        width=width_vals[0] if constant else None,
        color=color,
        opacity=opacity,
        cap=ir.LineCap.SQUARE if tool == "highlighter" else ir.LineCap.ROUND,
        underlay=(tool == "highlighter"),
    )
    return ir.Stroke(
        x=xs, y=ys,
        tool=ir.ToolRef(family=family,
                        native=ir.NativeTool(FORMAT_ID, tool, {})),
        color=color,
        channels={ir.Channel.WIDTH: widths},
        appearance=appearance,
    )


def _text_from_xml(el: ElementTree.Element) -> ir.TextBlock:
    return ir.TextBlock(
        x=float(el.get("x", 0)),
        y=float(el.get("y", 0)),
        text=el.text or "",
        font_size=float(el.get("size", 12)),
        color=parse_color(el.get("color", "#000000ff")),
    )


class XoppReader:
    format_id = FORMAT_ID
    extensions = (".xopp", ".xoj")

    def detect(self, path: Path) -> bool:
        try:
            with open(path, "rb") as f:
                head = f.read(2)
            if head == b"\x1f\x8b":
                with gzip.open(path, "rb") as f:
                    head_xml = f.read(512)
            else:
                with open(path, "rb") as f:
                    head_xml = f.read(512)
            return b"<xournal" in head_xml
        except OSError:
            return False

    def read(self, path: Path) -> ir.Document:
        root = _read_xml(path)
        title_el = root.find("title")
        pages = []
        for page_el in root.findall("page"):
            w = float(page_el.get("width", 612))
            h = float(page_el.get("height", 792))
            background = None
            bg_el = page_el.find("background")
            if bg_el is not None:
                style = bg_el.get("style", "plain")
                kind = STYLE_TO_KIND.get(style, "")
                if kind:
                    background = ir.TemplateBackground(kind=kind, name=style)
                if bg_el.get("type") not in (None, "solid"):
                    _logger.warning("unsupported xopp background type %r",
                                    bg_el.get("type"))
            layers = []
            for layer_el in page_el.findall("layer"):
                strokes = [
                    s for s in (
                        _stroke_from_xml(el) for el in layer_el.findall("stroke")
                    ) if s is not None
                ]
                texts = [_text_from_xml(el) for el in layer_el.findall("text")]
                layers.append(ir.Layer(strokes=strokes, texts=texts,
                                       name=layer_el.get("name", "")))
            pages.append(ir.Page(
                bounds=ir.Rect(0.0, 0.0, w, h),
                point_scale=1.0,  # xopp coordinates are already points
                layers=layers,
                background=background,
            ))
        orientation = "portrait"
        if pages and pages[0].bounds.width > pages[0].bounds.height:
            orientation = "landscape"
        return ir.Document(
            format_id=FORMAT_ID,
            title=(title_el.text or "") if title_el is not None else "",
            orientation=orientation,
            pages=pages,
        )
