"""Render IR documents to SVG.

SVG is single-page: page 1 is written to the given path and additional
pages land beside it as `<stem>-p2.svg`, `<stem>-p3.svg`, ...

Structure mirrors the PDF backend: template background first, then
underlay strokes (highlighters) across layers in order, then ordinary
ink; strokes whose first alpha <= 0 are skipped, and a page with no
drawable strokes becomes an empty (blank) SVG. Underlay ordering forces
two passes over the layers, so a layer can appear as two `<g>` elements
(both tagged `data-rmi-layer`).

Differences from the PDF backend, by design:
- Variable-width strokes are not split into piecewise-constant runs;
  they are tessellated into a single filled outline polygon offset by
  the per-point WIDTH channel (round caps become 8-segment fans).
- Per-point ALPHA cannot vary within one SVG element: the FIRST point's
  alpha is applied to the whole stroke.
- With `embed_raw` (default True) each stroke element carries
  `data-rmi-tool` / `data-rmi-pressure` / `data-rmi-width` attributes so
  the file stays debuggable and approximately re-ingestable.

Fidelity: EXACT consumes reader-supplied appearance; NATIVE restyles
every stroke from its tool family; RAW is rejected.
"""
from __future__ import annotations

import math
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from .. import ir

SVG_NS = "http://www.w3.org/2000/svg"
CAP_FAN_SEGMENTS = 8  # semicircle approximation for round outline caps


def _n(v: float) -> str:
    """Compact fixed-point number (2 decimals, trailing zeros stripped)."""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _hex_rgb(r: float, g: float, b: float) -> str:
    clamp = lambda c: min(255, max(0, round(c * 255)))
    return f"#{clamp(r):02x}{clamp(g):02x}{clamp(b):02x}"


def _hex(color: ir.Color) -> str:
    return _hex_rgb(color.r, color.g, color.b)


def _drawable(stroke: ir.Stroke) -> bool:
    if not stroke.x:
        return False
    alphas = stroke.channels.get(ir.Channel.ALPHA)
    a0 = alphas[0] if alphas else (
        stroke.appearance.opacity if stroke.appearance else 1.0
    )
    return a0 > 0


def _blend_style(app: ir.StrokeAppearance | None) -> str:
    if app is None or app.blend is ir.BlendMode.NORMAL:
        return ""
    return f' style="mix-blend-mode:{app.blend.value}"'


def _raw_attrs(stroke: ir.Stroke) -> str:
    parts = [f" data-rmi-tool={quoteattr(stroke.tool.family.value)}"]
    for channel, attr in ((ir.Channel.PRESSURE, "data-rmi-pressure"),
                          (ir.Channel.WIDTH, "data-rmi-width")):
        values = stroke.channels.get(channel)
        if values:
            joined = " ".join(str(round(v, 3)) for v in values)
            parts.append(f" {attr}={quoteattr(joined)}")
    return "".join(parts)


# --- variable-width outline tessellation ------------------------------------

def _cap_fan(cx: float, cy: float, r: float, nrm: tuple[float, float],
             d: tuple[float, float]) -> list[tuple[float, float]]:
    """Interior points of a semicircle from +nrm to -nrm bulging toward d."""
    pts = []
    for k in range(1, CAP_FAN_SEGMENTS):
        t = math.pi * k / CAP_FAN_SEGMENTS
        pts.append((cx + r * (nrm[0] * math.cos(t) + d[0] * math.sin(t)),
                    cy + r * (nrm[1] * math.cos(t) + d[1] * math.sin(t))))
    return pts


def outline_polygon(xs: list[float], ys: list[float], half: list[float],
                    round_caps: bool) -> list[tuple[float, float]] | None:
    """Closed outline of a variable-width polyline (point units = input).

    Each point is offset perpendicular to its local direction: interior
    points use the normalized average of adjacent segment directions,
    endpoints their single segment; zero-length segments reuse the last
    valid direction. Returns None when every point coincides (caller
    should draw a dot instead).
    """
    n = len(xs)
    dirs: list[tuple[float, float] | None] = []
    last = None
    for i in range(n - 1):
        dx, dy = xs[i + 1] - xs[i], ys[i + 1] - ys[i]
        length = math.hypot(dx, dy)
        if length > 1e-9:
            last = (dx / length, dy / length)
        dirs.append(last)
    first = next((d for d in dirs if d is not None), None)
    if first is None:
        return None
    dirs = [d if d is not None else first for d in dirs]

    normals: list[tuple[float, float]] = []
    for i in range(n):
        if i == 0:
            d = dirs[0]
        elif i == n - 1:
            d = dirs[-1]
        else:
            sx = dirs[i - 1][0] + dirs[i][0]
            sy = dirs[i - 1][1] + dirs[i][1]
            length = math.hypot(sx, sy)
            d = (sx / length, sy / length) if length > 1e-9 else dirs[i]
        normals.append((-d[1], d[0]))

    fwd = [(xs[i] + normals[i][0] * half[i], ys[i] + normals[i][1] * half[i])
           for i in range(n)]
    rev = [(xs[i] - normals[i][0] * half[i], ys[i] - normals[i][1] * half[i])
           for i in range(n)]
    pts = list(fwd)
    if round_caps:
        pts += _cap_fan(xs[-1], ys[-1], half[-1], normals[-1], dirs[-1])
    pts += rev[::-1]
    if round_caps:
        n0 = (-normals[0][0], -normals[0][1])
        d0 = (-dirs[0][0], -dirs[0][1])
        pts += _cap_fan(xs[0], ys[0], half[0], n0, d0)
    return pts


# --- element emission --------------------------------------------------------

def _circle(x: float, y: float, r: float, fill: str, alpha: float,
            extra: str) -> str:
    return (f'<circle cx="{_n(x)}" cy="{_n(y)}" r="{_n(r)}" '
            f'fill="{fill}" fill-opacity="{_n(alpha)}"{extra}/>')


def _path_d(pts: list[tuple[float, float]], close: bool) -> str:
    d = f"M {_n(pts[0][0])} {_n(pts[0][1])}"
    d += "".join(f" L {_n(x)} {_n(y)}" for x, y in pts[1:])
    return d + " Z" if close else d


def _stroke_element(stroke: ir.Stroke, x0: float, y0: float, scale: float,
                    embed_raw: bool) -> str:
    app = stroke.appearance
    xs = [(x - x0) * scale for x in stroke.x]
    ys = [(y - y0) * scale for y in stroke.y]
    alphas = stroke.channels.get(ir.Channel.ALPHA)
    alpha = alphas[0] if alphas else (app.opacity if app else 1.0)
    color = _hex(app.color if app else stroke.color)
    extra = _blend_style(app) + (_raw_attrs(stroke) if embed_raw else "")
    cap = app.cap.value if app else ir.LineCap.ROUND.value
    mode = app.mode if app else ir.GeometryMode.STROKED_VARIABLE

    if mode is ir.GeometryMode.STROKED_CONSTANT:
        width = (app.width if app.width else 1.0) * scale
        if len(xs) < 2:
            return _circle(xs[0], ys[0], width / 2, color, alpha, extra)
        return (f'<path d="{_path_d(list(zip(xs, ys)), close=False)}" '
                f'fill="none" stroke="{color}" stroke-width="{_n(width)}" '
                f'stroke-opacity="{_n(alpha)}" stroke-linecap="{cap}" '
                f'stroke-linejoin="round"{extra}/>')

    widths = stroke.channels.get(ir.Channel.WIDTH)
    if widths:
        half = [w * scale / 2 for w in widths]
    else:
        half = [(app.width if app and app.width else 1.0) * scale / 2] * len(xs)
    poly = outline_polygon(xs, ys, half, round_caps=(cap == "round"))
    if poly is None:  # all points coincident
        return _circle(xs[0], ys[0], max(half), color, alpha, extra)
    return (f'<path d="{_path_d(poly, close=True)}" fill="{color}" '
            f'fill-opacity="{_n(alpha)}"{extra}/>')


def _text_element(t: ir.TextBlock, x0: float, y0: float,
                  scale: float) -> str:
    fill = _hex(t.color) if t.color else "#000000"
    size = t.font_size or 12.0
    return (f'<text x="{_n((t.x - x0) * scale)}" y="{_n((t.y - y0) * scale)}" '
            f'font-size="{_n(size)}" fill="{fill}">{escape(t.text)}</text>')


def _template_elements(bg: ir.TemplateBackground, bounds: ir.Rect,
                       scale: float) -> list[str]:
    """SVG port of render/pdf.py:_draw_template — tiled over the (possibly
    grown) bounds, anchored at the source origin."""
    if bg.kind not in ("dots", "lines", "grid") or bg.pitch <= 0:
        return []
    gray = _hex_rgb(bg.gray, bg.gray, bg.gray)
    step = bg.pitch
    tx = lambda x: _n((x - bounds.x_min) * scale)
    ty = lambda y: _n((y - bounds.y_min) * scale)
    out = ['<g data-rmi-template=%s>' % quoteattr(bg.kind)]
    if bg.kind == "dots":
        r = _n(bg.dot_radius * scale)
        y = step * math.ceil(bounds.y_min / step)
        while y < bounds.y_max:
            x = step * math.ceil(bounds.x_min / step)
            while x < bounds.x_max:
                out.append(f'<circle cx="{tx(x)}" cy="{ty(y)}" r="{r}" '
                           f'fill="{gray}"/>')
                x += step
            y += step
    else:
        w = _n(bg.line_width * scale)
        line = (f'<line x1="{{}}" y1="{{}}" x2="{{}}" y2="{{}}" '
                f'stroke="{gray}" stroke-width="{w}"/>')
        y = step * math.ceil(bounds.y_min / step)
        while y < bounds.y_max:
            out.append(line.format(tx(bounds.x_min), ty(y),
                                   tx(bounds.x_max), ty(y)))
            y += step
        if bg.kind == "grid":
            x = step * math.ceil(bounds.x_min / step)
            while x < bounds.x_max:
                out.append(line.format(tx(x), ty(bounds.y_min),
                                       tx(x), ty(bounds.y_max)))
                x += step
    out.append("</g>")
    return out


def _page_svg(page: ir.Page, embed_raw: bool, templates: bool) -> str:
    bounds, scale = page.bounds, page.point_scale
    w, h = bounds.width * scale, bounds.height * scale
    out = [f'<svg xmlns="{SVG_NS}" viewBox="0 0 {_n(w)} {_n(h)}" '
           f'width="{_n(w)}pt" height="{_n(h)}pt">']

    layers = [ly for ly in page.layers if ly.visible]
    if any(_drawable(st) for ly in layers for st in ly.strokes):
        if templates and isinstance(page.background, ir.TemplateBackground):
            out += _template_elements(page.background, bounds, scale)

        def emit_pass(want_underlay: bool) -> None:
            for layer in layers:
                strokes = [
                    st for st in layer.strokes if _drawable(st)
                    and bool(st.appearance and st.appearance.underlay)
                    is want_underlay
                ]
                texts = layer.texts if not want_underlay else []
                if not strokes and not texts:
                    continue
                out.append("<g data-rmi-layer=%s>" % quoteattr(layer.name))
                for st in strokes:
                    out.append(_stroke_element(st, bounds.x_min, bounds.y_min,
                                               scale, embed_raw))
                for t in texts:
                    out.append(_text_element(t, bounds.x_min, bounds.y_min,
                                             scale))
                out.append("</g>")

        emit_pass(want_underlay=True)  # beneath ink (~ official /Darken)
        emit_pass(want_underlay=False)
    out.append("</svg>")
    return "\n".join(out) + "\n"


def _page_path(path: Path, index: int) -> Path:
    if index == 0:
        return path
    return path.with_name(f"{path.stem}-p{index + 1}{path.suffix}")


def render_document(doc: ir.Document, out_svg: Path, *, embed_raw: bool = True,
                    templates: bool = True) -> list[Path]:
    """Render an IR document to SVG file(s); returns the paths written."""
    out_svg = Path(out_svg)
    out_svg.parent.mkdir(parents=True, exist_ok=True)
    written = []
    for i, page in enumerate(doc.pages):
        path = _page_path(out_svg, i)
        path.write_text(_page_svg(page, embed_raw, templates),
                        encoding="utf-8")
        written.append(path)
    return written


class SvgWriter:
    """IR -> SVG via render_document (fidelity: exact and native)."""

    format_id = "svg"
    extensions = (".svg",)
    validated = True

    def write(self, doc: ir.Document, path: Path, fidelity,
              options: dict | None = None) -> None:
        from ..formats.base import Fidelity
        from ..ir.defaults import restyled

        options = options or {}
        if fidelity is Fidelity.RAW:
            raise ValueError(
                "SVG cannot hold raw ink dynamics; use .json (IR) or InkML"
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
        render_document(doc, Path(path),
                        embed_raw=options.get("embed_raw", True),
                        templates=options.get("templates", True))
