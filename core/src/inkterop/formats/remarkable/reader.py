"""Parse v6 .rm pages into the IR.

Coordinate model (validated against official Paper Pro exports): strokes are
stored in DISPLAY orientation — x horizontal centered on 0, y down from 0.
Nominal canvas 1620x2160 units portrait / 2160x1620 landscape; "adjustable
page height" grows y. Export scale ~= 685pt/2160u.

Each emitted stroke carries:
- WIDTH channel: the device-computed rendered width per point (point.width/4
  with a 0.5u floor) — the fidelity secret; never re-derive from pressure.
- ALPHA channel only where alpha varies per point (pencil); constant
  opacity lives in appearance.opacity.
- Raw PRESSURE (0-1), SPEED (device units), TILT_AZIMUTH (radians): the
  "raw" fidelity payload the legacy renderer used to discard.
Text-anchor handling adapted from rmc.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

from rmscene import CrdtId, SceneTree, read_tree
from rmscene import scene_items as si
from rmscene.text import TextDocument

from ... import ir
from .pens import HIGHLIGHT_TOOLS, PenModel
from .templates import resolve_template

_logger = logging.getLogger(__name__)

FORMAT_ID = "remarkable"

CANVAS_W = 1620
CANVAS_H = 2160
POINT_SCALE = 685.0 / 2160.0  # canvas units -> PDF points (official export)
GROWTH_PADDING = 48

TEXT_TOP_Y = -88
LINE_HEIGHT = 70
ANCHOR_TOP = CrdtId(0, 281474976710654)
ANCHOR_BOTTOM = CrdtId(0, 281474976710655)

# rmscene Pen -> neutral tool family
_FAMILY = {
    si.Pen.BALLPOINT_1: ir.ToolFamily.BALLPOINT,
    si.Pen.BALLPOINT_2: ir.ToolFamily.BALLPOINT,
    si.Pen.CALIGRAPHY: ir.ToolFamily.CALLIGRAPHY,
    si.Pen.ERASER: ir.ToolFamily.ERASER,
    si.Pen.ERASER_AREA: ir.ToolFamily.ERASER,
    si.Pen.FINELINER_1: ir.ToolFamily.FINELINER,
    si.Pen.FINELINER_2: ir.ToolFamily.FINELINER,
    si.Pen.HIGHLIGHTER_1: ir.ToolFamily.HIGHLIGHTER,
    si.Pen.HIGHLIGHTER_2: ir.ToolFamily.HIGHLIGHTER,
    si.Pen.MARKER_1: ir.ToolFamily.MARKER,
    si.Pen.MARKER_2: ir.ToolFamily.MARKER,
    si.Pen.MECHANICAL_PENCIL_1: ir.ToolFamily.MECHANICAL_PENCIL,
    si.Pen.MECHANICAL_PENCIL_2: ir.ToolFamily.MECHANICAL_PENCIL,
    si.Pen.PAINTBRUSH_1: ir.ToolFamily.BRUSH,
    si.Pen.PAINTBRUSH_2: ir.ToolFamily.BRUSH,
    si.Pen.PENCIL_1: ir.ToolFamily.PENCIL,
    si.Pen.PENCIL_2: ir.ToolFamily.PENCIL,
    si.Pen.SHADER: ir.ToolFamily.SHADER,
}

# Pens whose per-point alpha varies (everything else is stroke-constant).
_VARIABLE_ALPHA = {si.Pen.PENCIL_1, si.Pen.PENCIL_2}


def tool_family(pen: si.Pen) -> ir.ToolFamily:
    return _FAMILY.get(pen, ir.ToolFamily.UNKNOWN)


def _anchor_positions(text: si.Text | None) -> dict:
    pos = {}
    if text is not None:
        doc = TextDocument.from_scene_item(text)
        y = text.pos_y + TEXT_TOP_Y
        top = y
        for p in doc.contents:
            pos[p.start_id] = y
            for subp in p.contents:
                for k in subp.i:
                    pos[k] = y
            y += LINE_HEIGHT
        pos[ANCHOR_TOP] = top
        pos[ANCHOR_BOTTOM] = y
    else:
        pos[ANCHOR_TOP] = 0
        pos[ANCHOR_BOTTOM] = CANVAS_H
    return pos


def _group_anchor(group: si.Group, anchor_pos: dict) -> tuple[float, float]:
    ax = ay = 0.0
    if group.anchor_id is not None:
        if group.anchor_origin_x is not None:
            ax = group.anchor_origin_x.value
        ay = anchor_pos.get(group.anchor_id.value, 0.0)
    return ax, ay


def _stroke_from_line(line: si.Line, dx: float, dy: float,
                      pen_style: str) -> ir.Stroke | None:
    pts = line.points
    if not pts:
        return None
    pen = PenModel(line.tool, line.color, line.color_rgba,
                   line.thickness_scale, pen_style)
    constant_width = pen.constant_width
    is_highlight = pen.is_highlight

    xs = [p.x + dx for p in pts]
    ys = [p.y + dy for p in pts]
    widths = [pen.width(p) for p in pts]
    channels: dict[ir.Channel, list[float]] = {
        ir.Channel.WIDTH: widths,
        ir.Channel.PRESSURE: [p.pressure / 255.0 for p in pts],
        ir.Channel.SPEED: [float(p.speed) for p in pts],
        ir.Channel.TILT_AZIMUTH: [p.direction * (math.pi * 2) / 255 for p in pts],
    }

    if line.tool in _VARIABLE_ALPHA:
        channels[ir.Channel.ALPHA] = [pen.alpha(p) for p in pts]
        opacity = 1.0
    else:
        opacity = pen.alpha(pts[0])

    render_color = ir.Color(*pen.color(pts[0]))
    extra: dict = {}
    if pen_style == "rmc":
        point_rgb = [pen.color(p) for p in pts]
        if any(c != point_rgb[0] for c in point_rgb):
            extra["inkterop"] = {"point_rgb": [list(c) for c in point_rgb]}

    appearance = ir.StrokeAppearance(
        mode=(ir.GeometryMode.STROKED_CONSTANT if constant_width
              else ir.GeometryMode.STROKED_VARIABLE),
        width=widths[0] if constant_width else None,
        color=render_color,
        opacity=opacity,
        blend=ir.BlendMode.DARKEN if is_highlight else ir.BlendMode.NORMAL,
        cap=ir.LineCap.SQUARE if pen.cap == "square" else ir.LineCap.ROUND,
        join=ir.LineCap.ROUND,
        underlay=is_highlight,
    )
    return ir.Stroke(
        x=xs,
        y=ys,
        tool=ir.ToolRef(
            family=tool_family(line.tool),
            native=ir.NativeTool(
                FORMAT_ID,
                int(line.tool.value),
                {
                    "color": int(line.color.value),
                    "color_rgba": list(line.color_rgba) if line.color_rgba else None,
                    "thickness_scale": line.thickness_scale,
                    # device texture phase (pencil grain, brush); the
                    # writer restores it so round-trips render alike on-app
                    "starting_length": line.starting_length,
                },
            ),
        ),
        color=ir.Color(*pen.rgb),
        channels=channels,
        appearance=appearance,
        extra=extra,
    )


def _collect(group: si.Group, anchor_pos: dict, dx: float, dy: float,
             out: list[ir.Stroke], pen_style: str) -> None:
    ax, ay = _group_anchor(group, anchor_pos)
    dx, dy = dx + ax, dy + ay
    for child_id in group.children:
        child = group.children[child_id]
        if isinstance(child, si.Group):
            _collect(child, anchor_pos, dx, dy, out, pen_style)
        elif isinstance(child, si.Line):
            stroke = _stroke_from_line(child, dx, dy, pen_style)
            if stroke is not None:
                out.append(stroke)


def drawable(stroke: ir.Stroke) -> bool:
    """Whether the legacy renderer would paint this stroke at all."""
    alphas = stroke.channels.get(ir.Channel.ALPHA)
    a0 = alphas[0] if alphas else (
        stroke.appearance.opacity if stroke.appearance else 1.0
    )
    return a0 > 0


def _page_bounds(strokes: list[ir.Stroke], landscape: bool) -> ir.Rect:
    """Nominal canvas, extended by out-of-canvas points and grown-page y."""
    w, h = (CANVAS_H, CANVAS_W) if landscape else (CANVAS_W, CANVAS_H)
    painted = [s for s in strokes if drawable(s)]
    xs = [x for s in painted for x in s.x]
    ys = [y for s in painted for y in s.y]
    x_min = min([-w / 2] + xs)
    x_max = max([w / 2] + xs)
    y_min = min([0.0] + ys)
    y_max = float(h)
    if ys and max(ys) > h - GROWTH_PADDING:
        y_max = max(ys) + GROWTH_PADDING
    return ir.Rect(x_min, y_min, x_max, y_max)


def read_page(rm_path: Path, landscape: bool = False, template: str = "",
              pen_style: str = "faithful") -> ir.Page:
    """Parse one .rm page file into an IR page."""
    with open(rm_path, "rb") as f:
        tree: SceneTree = read_tree(f)
    anchor_pos = _anchor_positions(tree.root_text)
    strokes: list[ir.Stroke] = []
    _collect(tree.root, anchor_pos, 0.0, 0.0, strokes, pen_style)
    return ir.Page(
        bounds=_page_bounds(strokes, landscape),
        point_scale=POINT_SCALE,
        layers=[ir.Layer(strokes=strokes)],
        background=resolve_template(template),
        extra={"remarkable": {"landscape": landscape, "template": template}},
    )


def read_library_document(doc, pen_style: str = "faithful") -> ir.Document:
    """library.Document (desktop-cache entry) -> full IR document.

    Missing page files become empty pages (same as the mirror's behavior).
    """
    landscape = doc.orientation == "landscape"
    pages = []
    for uuid, template in zip(doc.page_uuids, doc.page_templates):
        rm_path = doc.dir / f"{uuid}.rm"
        if rm_path.exists():
            try:
                pages.append(read_page(rm_path, landscape=landscape,
                                       template=template, pen_style=pen_style))
                continue
            except Exception:
                _logger.warning("failed to parse %s; empty page", rm_path,
                                exc_info=True)
        pages.append(ir.Page(bounds=ir.Rect(0, 0, 1, 1),
                             point_scale=POINT_SCALE))
    return ir.Document(
        format_id=FORMAT_ID,
        title=doc.name,
        orientation=doc.orientation,
        pages=pages,
        metadata={"uuid": doc.uuid, "file_type": doc.file_type},
    )


_V6_MAGIC = b"reMarkable .lines file, version=6"


class RemarkableReader:
    """Single .rm v6 page file -> one-page IR document."""

    format_id = FORMAT_ID
    extensions = (".rm",)

    def detect(self, path: Path) -> bool:
        try:
            with open(path, "rb") as f:
                return f.read(len(_V6_MAGIC)) == _V6_MAGIC
        except OSError:
            return False

    def read(self, path: Path) -> ir.Document:
        return ir.Document(
            format_id=FORMAT_ID,
            title=path.stem,
            pages=[read_page(path)],
        )


__all__ = [
    "FORMAT_ID",
    "HIGHLIGHT_TOOLS",
    "POINT_SCALE",
    "RemarkableReader",
    "drawable",
    "read_library_document",
    "read_page",
    "tool_family",
]
