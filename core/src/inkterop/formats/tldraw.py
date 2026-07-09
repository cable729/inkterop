"""tldraw .tldr (JSON) -> IR. Reader only.

tldraw's file format is JSON and its schema is publicly documented at
tldraw.dev; the app source is under a custom source-visible license, so
this reader is built ONLY from the docs pages plus hand-made sample
files — no tldraw source code was read (cf. the GPL boundary note in
CLAUDE.md; same policy).

Container [verified against the documented shape of a .tldr file]:
`{"tldrawFileFormatVersion": 1, "schema": {...}, "records": [...]}`.
Records carry `typeName` ("document", "page", "shape", "camera",
"instance", ...). Shapes belong to a page via `parentId`
("page:<id>", or "shape:<id>" when nested in a frame/group); page
records carry `name` and a fractional `index` key whose lexicographic
order is the page order [inferred].

Ink is `shape` records of type "draw" (pen) and "highlight"
(highlighter): `x`/`y` shape origin, `rotation` radians about the
origin, `props.segments[]` each `{type: "free"|"straight",
points: [{x, y, z}, ...]}` with points relative to the origin and
`z` = pressure 0-1 (0.5 constant when no pressure device;
`props.isPen` marks real stylus input) [inferred from tldraw.dev docs].
`props.color` is a palette token, `props.size` a width token
(s/m/l/xl), `props.isClosed` closes the polyline. "text" shapes carry
`props.text` or ProseMirror-style `props.richText`.

Palette-token hex values and the size->px / font-size tables below are
[verified 2026-07-09] against the published tldraw 3.13.1 npm packages
at runtime (palette/size exports read in-browser; ink thickness
measured on a mounted editor's getSvgString output — observation of
released artifacts, no source read; method + numbers in
docs/formats/tldraw.md). "geo"/"line"/"arrow"/other shapes are
skipped (kept-small decision; see docs/formats/tldraw.md). Coordinates
are CSS px on an infinite y-down canvas (`point_scale = 0.75`);
per-page content-bbox bounds like the excalidraw reader.

No writer: tldraw's record schema migrates frequently (per-record
version sequences); emitting stale records is how files break. Read
side only, by design.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path

from .. import ir

_logger = logging.getLogger(__name__)

FORMAT_ID = "tldraw"
PX_SCALE = 0.75  # CSS px -> pt

# Default-theme (light mode) solid colors [verified 2026-07-09 against
# the published @tldraw/tlschema 3.13.1 package's runtime
# DefaultColorThemePalette export — observation of the released
# artifact, no source read].
_PALETTE = {
    "black": "#1d1d1d",
    "grey": "#9fa8b2",
    "light-violet": "#e085f4",
    "violet": "#ae3ec9",
    "blue": "#4465e9",
    "light-blue": "#4ba1f1",
    "yellow": "#f1ac4b",
    "orange": "#e16919",
    "green": "#099268",
    "light-green": "#4cb05e",
    "light-red": "#f87777",
    "red": "#e03131",
    "white": "#ffffff",
}
# Highlighter swatches (srgb) — the app draws "highlight" shapes with
# these, NOT the solid colors [verified, same palette export].
_HIGHLIGHT_PALETTE = {
    "black": "#fddd00",
    "grey": "#cbe7f1",
    "light-violet": "#ff88ff",
    "violet": "#c77cff",
    "blue": "#10acff",
    "light-blue": "#00f4ff",
    "yellow": "#fddd00",
    "orange": "#ffa500",
    "green": "#00ffc8",
    "light-green": "#65f641",
    "light-red": "#ff7fa3",
    "red": "#ff636e",
    "white": "#ffffff",
}
# size token -> strokeWidth px [verified: tldraw 3.13.1 STROKE_SIZES].
_STROKE_SIZES = {"s": 2.0, "m": 3.5, "l": 5.0, "xl": 10.0}
# size token -> text font size px [verified: tldraw 3.13.1 FONT_SIZES].
_FONT_SIZES = {"s": 18.0, "m": 24.0, "l": 36.0, "xl": 44.0}

# Rendered ink thickness is NOT the STROKE_SIZES value. Measured on a
# mounted tldraw 3.13.1 editor via getSvgString probes (straight
# horizontal strokes; see docs/formats/tldraw.md):
#   draw, z=0.5 (neutral):  thickness = 1.374*STROKE_SIZES + 2.52
#     (5.27 / 7.33 / 9.39 / 16.26 px for s/m/l/xl — exact fit)
#   draw, z=1.0: 1.503x the neutral thickness (linear interp assumed
#     between measurements; below z=0.5 extrapolated [inferred])
#   highlight: thickness = 1.12 * FONT_SIZES (20.16/26.88/40.32/49.28),
#     drawn as two stacked passes opacity 0.35 + 0.82 of the highlight
#     swatch => combined coverage ~0.883.
_DRAW_WIDTH_SLOPE = 1.374
_DRAW_WIDTH_BASE = 2.52
_HIGHLIGHT_WIDTH_FACTOR = 1.12
_HIGHLIGHT_OPACITY = 0.883


def _draw_width(size_px: float, z: float) -> float:
    base = _DRAW_WIDTH_SLOPE * size_px + _DRAW_WIDTH_BASE
    return base * (1.0 + 1.006 * (z - 0.5))

_INK_TYPES = ("draw", "highlight")


def _color(token: str | None, highlight: bool = False) -> ir.Color:
    pal = _HIGHLIGHT_PALETTE if highlight else _PALETTE
    s = pal.get(str(token or "black"), pal["black"]).lstrip("#")
    return ir.Color(*(int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4)))


def _rich_text(node) -> str:
    """Plain text from a ProseMirror-style richText tree."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_rich_text(c) for c in node)
    if isinstance(node, dict):
        if isinstance(node.get("text"), str):
            return node["text"]
        parts = [_rich_text(c) for c in node.get("content") or []]
        return ("\n" if node.get("type") == "doc" else "").join(parts)
    return ""


def _shape_points(rec: dict) -> tuple[list[float], list[float], list[float]]:
    """Absolute xs/ys (+ z pressures) for a draw/highlight shape."""
    props = rec.get("props") or {}
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for seg in props.get("segments") or []:
        for p in (seg or {}).get("points") or []:
            x, y = float(p.get("x", 0)), float(p.get("y", 0))
            if xs and x == xs[-1] and y == ys[-1]:
                continue  # segments share junction points
            xs.append(x)
            ys.append(y)
            zs.append(float(p.get("z", 0.5)))
    if props.get("isClosed") and len(xs) > 2:
        xs.append(xs[0])
        ys.append(ys[0])
        zs.append(zs[0])
    angle = float(rec.get("rotation") or 0.0)
    if angle:
        c, s = math.cos(angle), math.sin(angle)
        for i, (x, y) in enumerate(zip(xs, ys)):
            xs[i], ys[i] = x * c - y * s, x * s + y * c
    x0, y0 = float(rec.get("x", 0)), float(rec.get("y", 0))
    return [x0 + x for x in xs], [y0 + y for y in ys], zs


def _ink_stroke(rec: dict) -> ir.Stroke | None:
    props = rec.get("props") or {}
    xs, ys, zs = _shape_points(rec)
    if not xs:
        return None
    kind = rec.get("type")
    is_highlight = kind == "highlight"
    scale = float(props.get("scale") or 1.0)
    size_px = _STROKE_SIZES.get(str(props.get("size", "m")), 3.5)
    opacity = float(rec.get("opacity", 1.0))
    is_pen = bool(props.get("isPen"))
    # z is always stored but is a constant 0.5 placeholder without a
    # stylus; it is only real signal on pen input.
    real_pressure = is_pen or any(z != 0.5 for z in zs)

    if is_highlight:
        # the app draws highlights with the highlight swatch at a fat
        # FONT_SIZES-derived width (measured — see module docstring)
        color = _color(props.get("color"), highlight=True)
        widths = [_HIGHLIGHT_WIDTH_FACTOR
                  * _FONT_SIZES.get(str(props.get("size", "m")), 24.0)
                  * scale] * len(xs)
        opacity *= _HIGHLIGHT_OPACITY
    else:
        color = _color(props.get("color"))
        widths = [_draw_width(size_px, z if real_pressure else 0.5) * scale
                  for z in zs]

    channels: dict = {ir.Channel.WIDTH: widths}
    if real_pressure:
        channels[ir.Channel.PRESSURE] = zs
    variable = max(widths) - min(widths) > 1e-9

    return ir.Stroke(
        x=xs, y=ys,
        tool=ir.ToolRef(
            family=(ir.ToolFamily.HIGHLIGHTER if is_highlight
                    else ir.ToolFamily.PEN),
            native=ir.NativeTool(FORMAT_ID, str(kind), {
                "color": props.get("color"),
                "size": props.get("size"),
                "isPen": is_pen,
                "isClosed": bool(props.get("isClosed")),
            }),
        ),
        color=color,
        channels=channels,
        appearance=ir.StrokeAppearance(
            mode=(ir.GeometryMode.STROKED_VARIABLE if variable
                  else ir.GeometryMode.STROKED_CONSTANT),
            width=None if variable else widths[0],
            color=color, opacity=opacity,
            cap=ir.LineCap.ROUND,
            underlay=is_highlight,
            blend=(ir.BlendMode.DARKEN if is_highlight
                   else ir.BlendMode.NORMAL),
        ),
    )


def _text_block(rec: dict) -> ir.TextBlock | None:
    props = rec.get("props") or {}
    text = props.get("text")
    if not isinstance(text, str) or not text:
        text = _rich_text(props.get("richText"))
    if not text:
        return None
    return ir.TextBlock(
        x=float(rec.get("x", 0)), y=float(rec.get("y", 0)),
        text=text,
        font_size=_FONT_SIZES.get(str(props.get("size", "m")), 24.0)
        * float(props.get("scale") or 1.0),
        color=_color(props.get("color")),
    )


def _page_of(rec: dict, shapes_by_id: dict) -> str | None:
    """Resolve a shape's owning page through frame/group parents."""
    for _ in range(64):  # cycle guard
        parent = str(rec.get("parentId") or "")
        if parent.startswith("page:"):
            return parent
        if parent.startswith("shape:") and parent in shapes_by_id:
            rec = shapes_by_id[parent]
            continue
        return None
    return None


def file_to_document(data: dict, title: str = "") -> ir.Document:
    records = [r for r in data.get("records") or [] if isinstance(r, dict)]
    page_recs = sorted(
        (r for r in records if r.get("typeName") == "page"),
        key=lambda r: str(r.get("index", "")))
    shape_recs = [r for r in records if r.get("typeName") == "shape"]
    shapes_by_id = {str(r.get("id")): r for r in shape_recs}
    if not page_recs:  # degenerate file: one implicit page
        page_recs = [{"id": None, "name": ""}]

    skipped: dict[str, int] = {}
    pages: list[ir.Page] = []
    for prec in page_recs:
        pid = prec.get("id")
        strokes: list[ir.Stroke] = []
        texts: list[ir.TextBlock] = []
        for rec in sorted(shape_recs, key=lambda r: str(r.get("index", ""))):
            if pid is not None and _page_of(rec, shapes_by_id) != pid:
                continue
            kind = str(rec.get("type"))
            if kind in _INK_TYPES:
                s = _ink_stroke(rec)
                if s is not None:
                    strokes.append(s)
            elif kind == "text":
                t = _text_block(rec)
                if t is not None:
                    texts.append(t)
            else:  # geo/line/arrow/frame/... — not modeled (see docs)
                skipped[kind] = skipped.get(kind, 0) + 1

        xs = [x for s in strokes for x in s.x] + [t.x for t in texts]
        ys = [y for s in strokes for y in s.y] + [t.y for t in texts]
        pad = 20.0
        bounds = (ir.Rect(min(xs) - pad, min(ys) - pad,
                          max(xs) + pad, max(ys) + pad)
                  if xs else ir.Rect(0.0, 0.0, 800.0, 600.0))
        pages.append(ir.Page(
            bounds=bounds, point_scale=PX_SCALE,
            layers=[ir.Layer(strokes=strokes, texts=texts)],
            extra={"name": str(prec.get("name", ""))},
        ))
    if skipped:
        _logger.info("tldraw: skipped unmodeled shapes: %s", skipped)
    return ir.Document(
        format_id=FORMAT_ID,
        title=title,
        pages=pages,
        metadata={
            "tldraw_file_version": data.get("tldrawFileFormatVersion"),
            "schema_version": (data.get("schema") or {}).get("schemaVersion"),
            "skipped_shapes": skipped,
        },
    )


class TldrawReader:
    format_id = FORMAT_ID
    extensions = (".tldr",)

    def detect(self, path: Path) -> bool:
        try:
            head = path.open("rb").read(4096)
            return (b'"tldrawFileFormatVersion"' in head
                    and head.lstrip()[:1] == b"{")
        except OSError:
            return False

    def read(self, path: Path) -> ir.Document:
        data = json.loads(path.read_text(encoding="utf-8"))
        return file_to_document(data, title=path.stem)
