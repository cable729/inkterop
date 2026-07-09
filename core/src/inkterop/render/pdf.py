"""Render IR documents to PDF via reportlab.

Drawing behavior is a quirk-exact port of the validated legacy renderer
(see render/primitives.py): underlay strokes (highlighters) draw beneath
ink to approximate the official export's /BM /Darken blend, widths are set
in source units with the unit->point scale folded into the CTM, and
variable-width strokes become piecewise-constant runs.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from reportlab.lib.colors import Color
from reportlab.pdfgen import canvas as rl_canvas

from .. import ir
from .primitives import Run, stroke_runs

_logger = logging.getLogger(__name__)

LETTER_LANDSCAPE = (792.0, 612.0)
LETTER_PORTRAIT = (612.0, 792.0)


@dataclass
class RenderConfig:
    pen_style: str = "faithful"  # faithful | rmc (consumed by the rM reader)
    normalize: str = "uniform"  # uniform | native
    target_landscape: tuple[float, float] = LETTER_LANDSCAPE
    target_portrait: tuple[float, float] = LETTER_PORTRAIT
    templates: bool = True


def _drawable(stroke: ir.Stroke) -> bool:
    if not stroke.x:
        return False
    alphas = stroke.channels.get(ir.Channel.ALPHA)
    a0 = alphas[0] if alphas else (
        stroke.appearance.opacity if stroke.appearance else 1.0
    )
    return a0 > 0


def _draw_template(c: rl_canvas.Canvas, bg: ir.TemplateBackground,
                   bounds: ir.Rect) -> None:
    """Tile the template over the (possibly grown) page bounds, anchored at
    the canvas origin so it stays put as the page extends."""
    if bg.kind not in ("dots", "lines", "grid"):
        return
    x_min, x_max = bounds.x_min, bounds.x_max
    y_min, y_max = bounds.y_min, bounds.y_max
    g = Color(bg.gray, bg.gray, bg.gray)
    step = bg.pitch
    if bg.kind == "dots":
        c.setFillColor(g)
        y = step * math.ceil(y_min / step)
        while y < y_max:
            x = step * math.ceil(x_min / step)
            while x < x_max:
                c.circle(x, y, bg.dot_radius, stroke=0, fill=1)
                x += step
            y += step
    else:
        c.setStrokeColor(g)
        c.setLineWidth(bg.line_width)
        y = step * math.ceil(y_min / step)
        while y < y_max:
            c.line(x_min, y, x_max, y)
            y += step
        if bg.kind == "grid":
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


def _draw_raster(c: rl_canvas.Canvas, raster: ir.RasterImage,
                 page: ir.Page) -> None:
    """Place a bitmap in page-unit space (CTM is y-flipped, so unflip)."""
    from io import BytesIO

    from reportlab.lib.utils import ImageReader

    b = raster.bounds or page.bounds
    img = ImageReader(BytesIO(raster.data))
    c.saveState()
    c.translate(b.x_min, b.y_max)
    c.scale(1, -1)
    c.drawImage(img, 0, 0, width=b.width, height=b.height,
                mask="auto", preserveAspectRatio=False)
    c.restoreState()


def _page_has_content(page: ir.Page) -> bool:
    if any(_drawable(st) for st in page.strokes()):
        return True
    if isinstance(page.background, ir.ImageBackground):
        return True
    return any(layer.raster is not None
               for layer in page.layers if layer.visible)


def _draw_page(c: rl_canvas.Canvas, page: ir.Page, landscape: bool,
               config: RenderConfig) -> None:
    bounds = page.bounds
    scale = page.point_scale
    content_w = bounds.width * scale
    content_h = bounds.height * scale

    if config.normalize == "uniform":
        page_w, page_h = (config.target_landscape if landscape
                          else config.target_portrait)
    else:
        page_w, page_h = content_w, content_h
    c.setPageSize((page_w, page_h))

    s = min(page_w / content_w, page_h / content_h)
    ox = (page_w - content_w * s) / 2
    oy = (page_h - content_h * s) / 2

    strokes = [st for st in page.strokes() if _drawable(st)]
    underlay = [st for st in strokes
                if st.appearance and st.appearance.underlay]
    ink = [st for st in strokes
           if not (st.appearance and st.appearance.underlay)]

    c.saveState()
    c.transform(s * scale, 0.0, 0.0, -s * scale,
                ox - s * scale * bounds.x_min,
                oy + content_h * s + s * scale * bounds.y_min)
    # Widths are set in source units; fold the point scale into the CTM so
    # setLineWidth(units) comes out correct on the page.
    if isinstance(page.background, ir.ImageBackground):
        _draw_raster(c, page.background.image, page)
    if config.templates and isinstance(page.background, ir.TemplateBackground):
        _draw_template(c, page.background, bounds)
    for layer in page.layers:
        if layer.visible and layer.raster is not None:
            _draw_raster(c, layer.raster, page)
    for st in underlay:  # beneath ink (~ official /Darken blend)
        _draw_runs(c, stroke_runs(st))
    for st in ink:
        _draw_runs(c, stroke_runs(st))
    c.restoreState()
    c.showPage()


class PdfWriter:
    """IR -> PDF via render_document (fidelity: exact and native)."""

    format_id = "pdf"
    extensions = (".pdf",)
    validated = True

    def write(self, doc: ir.Document, path: Path, fidelity,
              options: dict | None = None) -> None:
        from ..formats.base import Fidelity
        from ..ir.defaults import restyled

        options = options or {}
        if fidelity is Fidelity.RAW:
            raise ValueError(
                "PDF cannot hold raw ink dynamics; use .json (IR) or InkML"
            )
        if fidelity is Fidelity.NATIVE:
            doc = ir.Document(
                format_id=doc.format_id, title=doc.title,
                orientation=doc.orientation, attachments=doc.attachments,
                metadata=doc.metadata, extra=doc.extra,
                pages=[
                    ir.Page(
                        bounds=p.bounds, point_scale=p.point_scale,
                        background=p.background, extra=p.extra,
                        layers=[
                            ir.Layer(
                                strokes=[restyled(s) for s in layer.strokes],
                                texts=layer.texts, raster=layer.raster,
                                name=layer.name, visible=layer.visible,
                            )
                            for layer in p.layers
                        ],
                    )
                    for p in doc.pages
                ],
            )
        render_document(doc, path, options.get("render_config"))


def render_document(doc: ir.Document, out_pdf: Path,
                    config: RenderConfig | None = None) -> None:
    """Render an IR document to a PDF file."""
    config = config or RenderConfig()
    landscape = doc.orientation == "landscape"
    buf = BytesIO()
    c = rl_canvas.Canvas(buf)
    c.setPageCompression(1)
    blank = (config.target_landscape if landscape else config.target_portrait)
    for page in doc.pages:
        if not _page_has_content(page):
            c.setPageSize(blank)
            c.showPage()
            continue
        _draw_page(c, page, landscape, config)
    c.save()
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    out_pdf.write_bytes(buf.getvalue())
