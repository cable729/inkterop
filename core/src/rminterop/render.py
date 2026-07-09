"""Render v6 .rm pages to PDF.

Coordinate model (validated against official Paper Pro exports of a landscape
notebook): strokes are stored in DISPLAY orientation — x horizontal (centered
on 0), y vertical growing downward from 0. The nominal canvas is the Paper
Pro panel: 1620x2160 units portrait, 2160x1620 landscape. With "adjustable
page height" content extends y beyond the nominal height, which is why
official exports have variable page heights (export scale ~= 685pt/2160u).

Text-anchor handling adapted from rmc (MIT, github.com/ricklupton/rmc).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from reportlab.lib.colors import Color
from reportlab.pdfgen import canvas as rl_canvas
from rmscene import CrdtId, SceneTree, read_tree
from rmscene import scene_items as si
from rmscene.text import TextDocument

from .pens import PenModel

_logger = logging.getLogger(__name__)

# Paper Pro canvas units (portrait). rM1/rM2 documents use 1404x1872; the
# difference only affects the minimum page box, not stroke placement.
CANVAS_W = 1620
CANVAS_H = 2160
SCALE = 685.0 / 2160.0  # canvas units -> PDF points (matches official export)
GROWTH_PADDING = 48  # units the official export pads below the last stroke

TEXT_TOP_Y = -88
LINE_HEIGHT = 70
ANCHOR_TOP = CrdtId(0, 281474976710654)
ANCHOR_BOTTOM = CrdtId(0, 281474976710655)

LETTER_LANDSCAPE = (792.0, 612.0)
LETTER_PORTRAIT = (612.0, 792.0)


@dataclass
class RenderConfig:
    pen_style: str = "faithful"  # faithful | rmc
    normalize: str = "uniform"  # uniform | native
    # Target page size (pt) used when normalize == "uniform":
    target_landscape: tuple[float, float] = LETTER_LANDSCAPE
    target_portrait: tuple[float, float] = LETTER_PORTRAIT


@dataclass
class Stroke:
    segments: list  # list of (points [(x,y)...], Segment)


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


def _collect_strokes(group: si.Group, anchor_pos: dict, dx: float, dy: float,
                     out: list, config: RenderConfig) -> None:
    ax, ay = _group_anchor(group, anchor_pos)
    dx, dy = dx + ax, dy + ay
    for child_id in group.children:
        child = group.children[child_id]
        if isinstance(child, si.Group):
            _collect_strokes(child, anchor_pos, dx, dy, out, config)
        elif isinstance(child, si.Line):
            out.append(_stroke_segments(child, dx, dy, config))


def _stroke_segments(line: si.Line, dx: float, dy: float, config: RenderConfig) -> Stroke:
    pen = PenModel.create(line.tool, line.color, line.color_rgba,
                          line.thickness_scale, config.pen_style)
    segments = []
    pts: list[tuple[float, float]] = []
    seg = None
    for i, p in enumerate(line.points):
        if i % pen.segment_length == 0:
            new_seg = pen.segment(p.speed, p.direction, p.width, p.pressure)
            if pts and seg is not None:
                segments.append((pts, seg))
                pts = [pts[-1]]  # join segments
            seg = new_seg
        pts.append((p.x + dx, p.y + dy))
    if pts and seg is not None:
        segments.append((pts, seg))
    return Stroke(segments)


def page_strokes(rm_path: Path, config: RenderConfig) -> list[Stroke]:
    with open(rm_path, "rb") as f:
        tree: SceneTree = read_tree(f)
    anchor_pos = _anchor_positions(tree.root_text)
    strokes: list[Stroke] = []
    _collect_strokes(tree.root, anchor_pos, 0.0, 0.0, strokes, config)
    return strokes


def _page_bounds(strokes: list[Stroke], landscape: bool) -> tuple:
    """Canvas box extended by any content that grew past it (units)."""
    w, h = (CANVAS_H, CANVAS_W) if landscape else (CANVAS_W, CANVAS_H)
    xs = [x for s in strokes for pts, _ in s.segments for x, _ in pts]
    ys = [y for s in strokes for pts, _ in s.segments for _, y in pts]
    x_min = min([-w / 2] + xs)
    x_max = max([w / 2] + xs)
    y_min = min([0.0] + ys)
    y_max = float(h)
    if ys and max(ys) > h - GROWTH_PADDING:
        y_max = max(ys) + GROWTH_PADDING
    return x_min, x_max, y_min, y_max


def _draw_page(c: rl_canvas.Canvas, strokes: list[Stroke],
               landscape: bool, config: RenderConfig) -> None:
    x_min, x_max, y_min, y_max = _page_bounds(strokes, landscape)
    content_w = (x_max - x_min) * SCALE
    content_h = (y_max - y_min) * SCALE

    if config.normalize == "uniform":
        page_w, page_h = (config.target_landscape if landscape
                          else config.target_portrait)
    else:
        page_w, page_h = content_w, content_h
    c.setPageSize((page_w, page_h))

    # Fit content box into the page, centered, preserving aspect.
    s = min(page_w / content_w, page_h / content_h)
    ox = (page_w - content_w * s) / 2
    oy = (page_h - content_h * s) / 2

    c.saveState()
    # screen (x, y) -> PDF: X = ox + s*SCALE*(x - x_min); Y flips (PDF y is up)
    c.transform(s * SCALE, 0.0, 0.0, -s * SCALE,
                ox - s * SCALE * x_min, oy + content_h * s + s * SCALE * y_min)
    for stroke in strokes:
        for pts, seg in stroke.segments:
            if seg.alpha <= 0 or len(pts) < 2:
                continue
            c.setLineWidth(max(seg.width, 0.1))
            c.setStrokeColor(Color(*seg.rgb, alpha=seg.alpha))
            c.setLineCap(1 if seg.cap == "round" else 2)
            c.setLineJoin(1)
            path = c.beginPath()
            path.moveTo(*pts[0])
            for pt in pts[1:]:
                path.lineTo(*pt)
            c.drawPath(path, stroke=1, fill=0)
    c.restoreState()
    c.showPage()


def render_notebook(page_paths: list[Path], out_pdf: Path, landscape: bool,
                    config: RenderConfig | None = None) -> None:
    """Render a notebook's .rm pages to a single PDF."""
    config = config or RenderConfig()
    buf = BytesIO()
    c = rl_canvas.Canvas(buf)
    c.setPageCompression(1)
    blank = (config.target_landscape if landscape else config.target_portrait)
    for rm_path in page_paths:
        strokes: list[Stroke] = []
        if rm_path.exists():
            try:
                strokes = page_strokes(rm_path, config)
            except Exception:
                _logger.warning("failed to parse %s; blank page", rm_path, exc_info=True)
        if not strokes:
            c.setPageSize(blank)
            c.showPage()
            continue
        _draw_page(c, strokes, landscape, config)
    c.save()
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    out_pdf.write_bytes(buf.getvalue())
