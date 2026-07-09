"""Render v6 .rm pages to PDF.

Coordinate model (validated against official Paper Pro exports): strokes are
stored in DISPLAY orientation — x horizontal centered on 0, y down from 0.
Nominal canvas 1620x2160 units portrait / 2160x1620 landscape; "adjustable
page height" grows y. Export scale ~= 685pt/2160u.

Stroke widths come straight from the file (point.width/4 units — the device
precomputes rendered width). Highlighter strokes draw beneath ink to mimic
the official export's /Darken blend. Text-anchor handling adapted from rmc.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from reportlab.lib.colors import Color
from reportlab.pdfgen import canvas as rl_canvas
from rmscene import CrdtId, SceneTree, read_tree
from rmscene import scene_items as si
from rmscene.text import TextDocument

from .pens import PenModel

_logger = logging.getLogger(__name__)

CANVAS_W = 1620
CANVAS_H = 2160
SCALE = 685.0 / 2160.0  # canvas units -> PDF points (matches official export)
GROWTH_PADDING = 48

TEXT_TOP_Y = -88
LINE_HEIGHT = 70
ANCHOR_TOP = CrdtId(0, 281474976710654)
ANCHOR_BOTTOM = CrdtId(0, 281474976710655)

LETTER_LANDSCAPE = (792.0, 612.0)
LETTER_PORTRAIT = (612.0, 792.0)

# Width tolerance (units) within which consecutive points share one polyline.
WIDTH_RUN_TOLERANCE = 0.35


@dataclass
class RenderConfig:
    pen_style: str = "faithful"  # faithful | rmc
    normalize: str = "uniform"  # uniform | native
    target_landscape: tuple[float, float] = LETTER_LANDSCAPE
    target_portrait: tuple[float, float] = LETTER_PORTRAIT
    templates: bool = True


@dataclass
class Run:
    points: list  # [(x, y), ...]
    width: float  # canvas units
    rgb: tuple
    alpha: float
    cap: str


@dataclass
class PageContent:
    ink: list = field(default_factory=list)  # [Run]
    highlights: list = field(default_factory=list)  # [Run]


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


def _collect(group: si.Group, anchor_pos: dict, dx: float, dy: float,
             out: PageContent, config: RenderConfig) -> None:
    ax, ay = _group_anchor(group, anchor_pos)
    dx, dy = dx + ax, dy + ay
    for child_id in group.children:
        child = group.children[child_id]
        if isinstance(child, si.Group):
            _collect(child, anchor_pos, dx, dy, out, config)
        elif isinstance(child, si.Line):
            _line_runs(child, dx, dy, out, config)


def _line_runs(line: si.Line, dx: float, dy: float, out: PageContent,
               config: RenderConfig) -> None:
    """Split a stroke into constant-width runs (device stores per-point width)."""
    pen = PenModel(line.tool, line.color, line.color_rgba,
                   line.thickness_scale, config.pen_style)
    pts = line.points
    if not pts:
        return
    dest = out.highlights if pen.is_highlight else out.ink
    p0 = pts[0]
    alpha = pen.alpha(p0)
    if alpha <= 0:
        return
    run = Run([(p0.x + dx, p0.y + dy)], pen.width(p0), pen.color(p0), alpha, pen.cap)
    for p in pts[1:]:
        w = run.width if pen.constant_width else pen.width(p)
        if abs(w - run.width) > WIDTH_RUN_TOLERANCE and len(run.points) > 1:
            dest.append(run)
            last = run.points[-1]
            run = Run([last], w, pen.color(p), pen.alpha(p), pen.cap)
        run.points.append((p.x + dx, p.y + dy))
    dest.append(run)


def page_content(rm_path: Path, config: RenderConfig) -> PageContent:
    with open(rm_path, "rb") as f:
        tree: SceneTree = read_tree(f)
    anchor_pos = _anchor_positions(tree.root_text)
    out = PageContent()
    _collect(tree.root, anchor_pos, 0.0, 0.0, out, config)
    return out


def _page_bounds(content: PageContent, landscape: bool) -> tuple:
    w, h = (CANVAS_H, CANVAS_W) if landscape else (CANVAS_W, CANVAS_H)
    xs = [x for r in content.ink + content.highlights for x, _ in r.points]
    ys = [y for r in content.ink + content.highlights for _, y in r.points]
    x_min = min([-w / 2] + xs)
    x_max = max([w / 2] + xs)
    y_min = min([0.0] + ys)
    y_max = float(h)
    if ys and max(ys) > h - GROWTH_PADDING:
        y_max = max(ys) + GROWTH_PADDING
    return x_min, x_max, y_min, y_max


TEMPLATE_GRAY = 0.62
DOT_SPACING = 39.0  # canvas units; approximates "Dots S" pitch
DOT_RADIUS = 1.7
LINE_SPACING = {"S": 55.0, "M": 78.0, "L": 110.0}


def _draw_template(c: rl_canvas.Canvas, name: str, bounds: tuple) -> None:
    """Approximate the common built-in page templates (dots/lines/grid).

    The pattern tiles over the full (possibly grown) page bounds, anchored at
    the canvas origin so it stays put as the page extends.
    """
    if not name or name == "Blank":
        return
    x_min, x_max, y_min, y_max = bounds
    g = Color(TEMPLATE_GRAY, TEMPLATE_GRAY, TEMPLATE_GRAY)
    if "Dots" in name:
        c.setFillColor(g)
        y = DOT_SPACING * math.ceil(y_min / DOT_SPACING)
        while y < y_max:
            x = DOT_SPACING * math.ceil(x_min / DOT_SPACING)
            while x < x_max:
                c.circle(x, y, DOT_RADIUS, stroke=0, fill=1)
                x += DOT_SPACING
            y += DOT_SPACING
    elif "Grid" in name or "Lines" in name:
        size = name.rsplit(" ", 1)[-1] if name and name[-1] in "SML" else "M"
        step = LINE_SPACING.get(size, 78.0)
        c.setStrokeColor(g)
        c.setLineWidth(0.6)
        y = step * math.ceil(y_min / step)
        while y < y_max:
            c.line(x_min, y, x_max, y)
            y += step
        if "Grid" in name:
            x = step * math.ceil(x_min / step)
            while x < x_max:
                c.line(x, y_min, x, y_max)
                x += step


def _draw_runs(c: rl_canvas.Canvas, runs: list[Run]) -> None:
    for run in runs:
        if len(run.points) < 2:
            # Dot: a zero-length round-cap segment renders nothing; use circle.
            x, y = run.points[0]
            c.setFillColor(Color(*run.rgb, alpha=run.alpha))
            c.circle(x, y, run.width / 2, stroke=0, fill=1)
            continue
        c.setLineWidth(run.width)
        c.setStrokeColor(Color(*run.rgb, alpha=run.alpha))
        c.setLineCap(1 if run.cap == "round" else 2)
        c.setLineJoin(1)
        path = c.beginPath()
        path.moveTo(*run.points[0])
        for pt in run.points[1:]:
            path.lineTo(*pt)
        c.drawPath(path, stroke=1, fill=0)


def _draw_page(c: rl_canvas.Canvas, content: PageContent, template: str,
               landscape: bool, config: RenderConfig) -> None:
    x_min, x_max, y_min, y_max = _page_bounds(content, landscape)
    content_w = (x_max - x_min) * SCALE
    content_h = (y_max - y_min) * SCALE

    if config.normalize == "uniform":
        page_w, page_h = (config.target_landscape if landscape
                          else config.target_portrait)
    else:
        page_w, page_h = content_w, content_h
    c.setPageSize((page_w, page_h))

    s = min(page_w / content_w, page_h / content_h)
    ox = (page_w - content_w * s) / 2
    oy = (page_h - content_h * s) / 2

    c.saveState()
    c.transform(s * SCALE, 0.0, 0.0, -s * SCALE,
                ox - s * SCALE * x_min, oy + content_h * s + s * SCALE * y_min)
    # Widths are set in canvas units; fold the point scale into the CTM so
    # setLineWidth(units) comes out correct on the page.
    if config.templates:
        _draw_template(c, template, (x_min, x_max, y_min, y_max))
    _draw_runs(c, content.highlights)  # beneath ink (~ official /Darken blend)
    _draw_runs(c, content.ink)
    c.restoreState()
    c.showPage()


def render_notebook(page_paths: list[Path], out_pdf: Path, landscape: bool,
                    config: RenderConfig | None = None,
                    templates: list[str] | None = None) -> None:
    """Render a notebook's .rm pages to a single PDF."""
    config = config or RenderConfig()
    templates = templates or [""] * len(page_paths)
    buf = BytesIO()
    c = rl_canvas.Canvas(buf)
    c.setPageCompression(1)
    blank = (config.target_landscape if landscape else config.target_portrait)
    for rm_path, template in zip(page_paths, templates):
        content = PageContent()
        if rm_path.exists():
            try:
                content = page_content(rm_path, config)
            except Exception:
                _logger.warning("failed to parse %s; blank page", rm_path, exc_info=True)
        if not (content.ink or content.highlights):
            c.setPageSize(blank)
            c.showPage()
            continue
        _draw_page(c, content, template, landscape, config)
    c.save()
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    out_pdf.write_bytes(buf.getvalue())
