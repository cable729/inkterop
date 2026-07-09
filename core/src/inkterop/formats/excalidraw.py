"""Excalidraw .excalidraw (JSON) <-> IR.

Open format (MIT app, schema documented at docs.excalidraw.com; field
facts below marked [inferred] until re-verified against an app-made
sample). Container: plain JSON `{"type": "excalidraw", "version": 2,
"elements": [...], "appState": {...}, "files": {...}}`.

Ink is `freedraw` elements: `x`/`y` element origin, `points` relative
[[dx, dy], ...], optional `pressures` [0-1] (absent/empty when
`simulatePressure` is true), `strokeColor` hex, `strokeWidth`,
`opacity` 0-100, `angle` radians (rotation about the element center).
`line`/`arrow` carry `points` too; `rectangle`/`ellipse`/`diamond` are
implicit shapes we flatten to outline polylines. `text` elements carry
`text`/`fontSize`. Canvas is infinite, y-down, CSS px
(`point_scale = 0.75`).

`strokeWidth` is NOT the rendered thickness of freedraw ink: the app
draws it via perfect-freehand, and the on-canvas thickness follows the
measured law in `_thickness_factor` ([verified] against
@excalidraw/excalidraw 0.18 `exportToSvg`, see
`docs/formats/excalidraw.md`). Only line/arrow/shape elements use
`strokeWidth` as a plain 1:1 stroke width.

Both directions accept RAW fidelity: pressure is the only raw channel
the format stores. At EXACT/NATIVE fidelity the writer re-encodes a
varying WIDTH channel into synthetic `pressures` through the inverse
rendering law, so the app reproduces per-point widths; RAW keeps the
source pressure values instead.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import median
from typing import Any

from .. import ir
from ._scale import unit_factor
from .base import Fidelity

FORMAT_ID = "excalidraw"
PX_SCALE = 0.75  # CSS px -> pt

_DEFAULT_STROKE = "#1e1e1e"

# Freedraw rendering law, measured on @excalidraw/excalidraw 0.18
# exportToSvg with constant-pressure probe strokes (fits the probes to
# 3 significant digits; see docs/formats/excalidraw.md):
#   thickness(p) = strokeWidth * 8.5 * sin(pi/2 * (0.5 + 0.6*(p - 0.5)))
# i.e. 8.08x at p=1.0, 6.01x at p=0.5. simulatePressure strokes measured
# ~6.9x on a uniform-speed probe (speed-dependent, approximate).
_FREEDRAW_SIZE = 8.5
_FREEDRAW_THINNING = 0.6
_SIMULATED_FACTOR = 6.9


def _thickness_factor(pressure: float) -> float:
    """Rendered freedraw thickness per unit strokeWidth at a pressure."""
    t = 0.5 + _FREEDRAW_THINNING * (pressure - 0.5)
    return _FREEDRAW_SIZE * math.sin(math.pi / 2.0 * t)


def _pressure_for_ratio(ratio: float) -> float:
    """Inverse of _thickness_factor: ratio = thickness / strokeWidth."""
    t = 2.0 / math.pi * math.asin(max(0.0, min(1.0, ratio / _FREEDRAW_SIZE)))
    return max(0.0, min(1.0, 0.5 + (t - 0.5) / _FREEDRAW_THINNING))


def _parse_color(hex_str: str | None) -> tuple[ir.Color, float]:
    s = (hex_str or _DEFAULT_STROKE).strip().lstrip("#")
    if hex_str in (None, "", "transparent"):
        return ir.Color(0, 0, 0), 0.0
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    try:
        r, g, b = (int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        a = int(s[6:8], 16) / 255.0 if len(s) >= 8 else 1.0
    except ValueError:
        return ir.Color(0, 0, 0), 1.0
    return ir.Color(r, g, b), a


def _hex(color: ir.Color) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        round(color.r * 255), round(color.g * 255), round(color.b * 255))


def _rotate(xs: list[float], ys: list[float], el: dict) -> None:
    angle = float(el.get("angle") or 0.0)
    if not angle:
        return
    cx = float(el.get("x", 0)) + float(el.get("width", 0)) / 2.0
    cy = float(el.get("y", 0)) + float(el.get("height", 0)) / 2.0
    c, s = math.cos(angle), math.sin(angle)
    for i, (x, y) in enumerate(zip(xs, ys)):
        dx, dy = x - cx, y - cy
        xs[i] = cx + dx * c - dy * s
        ys[i] = cy + dx * s + dy * c


def _shape_points(el: dict) -> tuple[list[float], list[float]]:
    """Outline polyline for implicit-geometry elements."""
    x0, y0 = float(el.get("x", 0)), float(el.get("y", 0))
    w, h = float(el.get("width", 0)), float(el.get("height", 0))
    kind = el["type"]
    if kind == "ellipse":
        n = 32
        cx, cy, rx, ry = x0 + w / 2, y0 + h / 2, w / 2, h / 2
        pts = [(cx + rx * math.cos(2 * math.pi * i / n),
                cy + ry * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
    elif kind == "diamond":
        pts = [(x0 + w / 2, y0), (x0 + w, y0 + h / 2),
               (x0 + w / 2, y0 + h), (x0, y0 + h / 2), (x0 + w / 2, y0)]
    else:  # rectangle
        pts = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h),
               (x0, y0)]
    return [p[0] for p in pts], [p[1] for p in pts]


def _element_stroke(el: dict) -> ir.Stroke | None:
    kind = el.get("type")
    if kind in ("freedraw", "line", "arrow"):
        rel = el.get("points") or []
        if not rel:
            return None
        x0, y0 = float(el.get("x", 0)), float(el.get("y", 0))
        xs = [x0 + float(p[0]) for p in rel]
        ys = [y0 + float(p[1]) for p in rel]
    elif kind in ("rectangle", "ellipse", "diamond"):
        xs, ys = _shape_points(el)
        if not xs:
            return None
    else:
        return None
    _rotate(xs, ys, el)

    color, _ = _parse_color(el.get("strokeColor"))
    opacity = float(el.get("opacity", 100)) / 100.0
    stroke_width = float(el.get("strokeWidth", 2.0))
    family = ir.ToolFamily.PEN if kind == "freedraw" else ir.ToolFamily.UNKNOWN

    pressures = el.get("pressures") or []
    have_pressures = kind == "freedraw" and len(pressures) == len(xs) and pressures
    if kind == "freedraw":
        # decode strokeWidth through the measured rendering law
        if have_pressures:
            widths = [stroke_width * _thickness_factor(float(p))
                      for p in pressures]
        else:
            widths = [stroke_width * _SIMULATED_FACTOR] * len(xs)
    else:
        widths = [stroke_width] * len(xs)  # shapes stroke 1:1
    variable = max(widths) - min(widths) > 1e-9

    channels: dict = {ir.Channel.WIDTH: widths}
    if have_pressures:
        channels[ir.Channel.PRESSURE] = [float(p) for p in pressures]

    return ir.Stroke(
        x=xs, y=ys,
        tool=ir.ToolRef(
            family=family,
            native=ir.NativeTool(FORMAT_ID, kind, {
                "strokeWidth": stroke_width,
                "simulatePressure": bool(el.get("simulatePressure")),
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
        ),
    )


def scene_to_document(scene: dict, title: str = "") -> ir.Document:
    strokes: list[ir.Stroke] = []
    texts: list[ir.TextBlock] = []
    for el in scene.get("elements") or []:
        if not isinstance(el, dict) or el.get("isDeleted"):
            continue
        if el.get("type") == "text":
            color, _ = _parse_color(el.get("strokeColor"))
            texts.append(ir.TextBlock(
                x=float(el.get("x", 0)), y=float(el.get("y", 0)),
                text=str(el.get("text", "")),
                font_size=float(el.get("fontSize", 20.0)),
                color=color,
            ))
            continue
        s = _element_stroke(el)
        if s is not None:
            strokes.append(s)

    xs = [x for s in strokes for x in s.x] + [t.x for t in texts]
    ys = [y for s in strokes for y in s.y] + [t.y for t in texts]
    pad = 20.0
    if xs:
        bounds = ir.Rect(min(xs) - pad, min(ys) - pad,
                         max(xs) + pad, max(ys) + pad)
    else:
        bounds = ir.Rect(0.0, 0.0, 800.0, 600.0)
    return ir.Document(
        format_id=FORMAT_ID,
        title=title,
        pages=[ir.Page(bounds=bounds, point_scale=PX_SCALE,
                       layers=[ir.Layer(strokes=strokes, texts=texts)])],
    )


def document_to_scene(doc: ir.Document,
                      fidelity: Fidelity = Fidelity.EXACT) -> dict:
    elements: list[dict] = []
    seq = 0
    for page in doc.pages:
        k = unit_factor(page, PX_SCALE)
        bx, by = page.bounds.x_min, page.bounds.y_min
        for layer in page.layers:
            if not layer.visible:
                continue
            for s in layer.strokes:
                if not s.x:
                    continue
                seq += 1
                xs = [(x - bx) * k for x in s.x]
                ys = [(y - by) * k for y in s.y]
                widths = s.channels.get(ir.Channel.WIDTH)
                if s.appearance is not None and s.appearance.width is not None:
                    target = s.appearance.width * k
                elif widths:
                    target = median(widths) * k
                else:
                    target = None
                color = s.appearance.color if s.appearance else s.color
                alphas = s.channels.get(ir.Channel.ALPHA)
                if alphas:
                    opacity = median(alphas)
                else:
                    opacity = s.appearance.opacity if s.appearance else 1.0
                pressures = s.channels.get(ir.Channel.PRESSURE)

                # Choose strokeWidth/pressures so the app's rendering law
                # reproduces the source thickness (see _thickness_factor).
                # Width-encoding wins over raw pressure at EXACT/NATIVE:
                # a constant-width pen with varying pressure must not
                # taper in-app. (excalidraw->excalidraw round-trips still
                # preserve pressures exactly: the reader derived WIDTH
                # from them, so the inversion returns the same values.)
                if (fidelity is not Fidelity.RAW and widths
                        and len(widths) == len(s.x)):
                    # per-point width -> synthetic pressures; the widest
                    # point renders at full pressure
                    stroke_width = (max(widths) * k) / _thickness_factor(1.0)
                    out_pressures = [
                        _pressure_for_ratio(w * k / stroke_width)
                        for w in widths]
                elif pressures and len(pressures) == len(s.x):
                    ref = median(pressures)
                    stroke_width = ((target if target is not None else 2.0)
                                    / _thickness_factor(ref))
                    out_pressures = [float(p) for p in pressures]
                elif target is not None:
                    stroke_width = target / _SIMULATED_FACTOR
                    out_pressures = []
                else:
                    stroke_width = 1.0  # app default "thin"
                    out_pressures = []
                elements.append({
                    "id": f"ink-{seq:04d}",
                    "type": "freedraw",
                    "x": xs[0], "y": ys[0],
                    "width": max(xs) - min(xs), "height": max(ys) - min(ys),
                    "angle": 0,
                    "strokeColor": _hex(color),
                    "backgroundColor": "transparent",
                    "fillStyle": "solid",
                    "strokeWidth": stroke_width,
                    "strokeStyle": "solid",
                    "roughness": 0,
                    "opacity": round(opacity * 100),
                    "groupIds": [], "frameId": None, "roundness": None,
                    "seed": seq, "version": 1, "versionNonce": seq,
                    "isDeleted": False, "boundElements": None,
                    "updated": 0, "link": None, "locked": False,
                    "points": [[x - xs[0], y - ys[0]]
                               for x, y in zip(xs, ys)],
                    "pressures": out_pressures,
                    "simulatePressure": not out_pressures,
                    "lastCommittedPoint": None,
                })
            for t in layer.texts:
                seq += 1
                size = (t.font_size or 20.0) * k
                elements.append({
                    "id": f"txt-{seq:04d}",
                    "type": "text",
                    "x": (t.x - bx) * k, "y": (t.y - by) * k,
                    "width": 8.0 * size * 0.6 * max(1, len(t.text)),
                    "height": size * 1.25,
                    "angle": 0,
                    "strokeColor": _hex(t.color or ir.Color(0, 0, 0)),
                    "backgroundColor": "transparent",
                    "fillStyle": "solid", "strokeWidth": 2,
                    "strokeStyle": "solid", "roughness": 0, "opacity": 100,
                    "groupIds": [], "frameId": None, "roundness": None,
                    "seed": seq, "version": 1, "versionNonce": seq,
                    "isDeleted": False, "boundElements": None,
                    "updated": 0, "link": None, "locked": False,
                    "text": t.text, "fontSize": size, "fontFamily": 1,
                    "textAlign": "left", "verticalAlign": "top",
                    "containerId": None, "originalText": t.text,
                    "autoResize": True, "lineHeight": 1.25,
                })
        break  # single infinite canvas: only page 1 (documented limitation)
    return {
        "type": "excalidraw",
        "version": 2,
        "source": "https://github.com/cable729/inkterop",
        "elements": elements,
        "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff"},
        "files": {},
    }


class ExcalidrawReader:
    format_id = FORMAT_ID
    extensions = (".excalidraw",)

    def detect(self, path: Path) -> bool:
        try:
            head = path.open("rb").read(4096)
            return b'"excalidraw"' in head and head.lstrip()[:1] == b"{"
        except OSError:
            return False

    def read(self, path: Path) -> ir.Document:
        scene = json.loads(path.read_text(encoding="utf-8"))
        return scene_to_document(scene, title=path.stem)


class ExcalidrawWriter:
    format_id = FORMAT_ID
    extensions = (".excalidraw",)
    # open-checked via @excalidraw/excalidraw 0.18.0 loadFromBlob (the
    # app's file-open path) + exportToSvg visual match, 2026-07-09
    # (docs/validated-writes.md)
    validated = True

    def write(self, doc: ir.Document, path: Path, fidelity: Fidelity,
              options: dict[str, Any] | None = None) -> None:
        scene = document_to_scene(doc, fidelity)
        path.write_text(json.dumps(scene, indent=2), encoding="utf-8")
