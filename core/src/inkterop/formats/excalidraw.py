"""Excalidraw .excalidraw (JSON) <-> IR.

Open format (MIT app, schema documented at docs.excalidraw.com; field
facts below marked [inferred] until re-verified against an app-made
sample). Container: plain JSON `{"type": "excalidraw", "version": 2,
"elements": [...], "appState": {...}, "files": {...}}`.

Ink is `freedraw` elements: `x`/`y` element origin, `points` relative
[[dx, dy], ...], optional `pressures` [0-1] (absent/empty when
`simulatePressure` is true), `strokeColor` hex, `strokeWidth` px,
`opacity` 0-100, `angle` radians (rotation about the element center).
`line`/`arrow` carry `points` too; `rectangle`/`ellipse`/`diamond` are
implicit shapes we flatten to outline polylines. `text` elements carry
`text`/`fontSize`. Canvas is infinite, y-down, CSS px
(`point_scale = 0.75`).

Both directions accept RAW fidelity: pressure is the only raw channel
the format stores. Writer ships validated=False pending an
excalidraw.com open-check (docs/validated-writes.md).
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
    width = float(el.get("strokeWidth", 2.0))
    family = ir.ToolFamily.PEN if kind == "freedraw" else ir.ToolFamily.UNKNOWN

    channels: dict = {ir.Channel.WIDTH: [width] * len(xs)}
    pressures = el.get("pressures") or []
    if kind == "freedraw" and len(pressures) == len(xs):
        channels[ir.Channel.PRESSURE] = [float(p) for p in pressures]

    return ir.Stroke(
        x=xs, y=ys,
        tool=ir.ToolRef(
            family=family,
            native=ir.NativeTool(FORMAT_ID, kind, {
                "strokeWidth": width,
                "simulatePressure": bool(el.get("simulatePressure")),
            }),
        ),
        color=color,
        channels=channels,
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT,
            width=width, color=color, opacity=opacity,
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
                if s.appearance is not None and s.appearance.width is not None:
                    width = s.appearance.width * k
                else:
                    widths = s.channels.get(ir.Channel.WIDTH)
                    width = (median(widths) * k) if widths else 2.0
                color = s.appearance.color if s.appearance else s.color
                opacity = s.appearance.opacity if s.appearance else 1.0
                pressures = s.channels.get(ir.Channel.PRESSURE)
                elements.append({
                    "id": f"ink-{seq:04d}",
                    "type": "freedraw",
                    "x": xs[0], "y": ys[0],
                    "width": max(xs) - min(xs), "height": max(ys) - min(ys),
                    "angle": 0,
                    "strokeColor": _hex(color),
                    "backgroundColor": "transparent",
                    "fillStyle": "solid",
                    "strokeWidth": width,
                    "strokeStyle": "solid",
                    "roughness": 0,
                    "opacity": round(opacity * 100),
                    "groupIds": [], "frameId": None, "roundness": None,
                    "seed": seq, "version": 1, "versionNonce": seq,
                    "isDeleted": False, "boundElements": None,
                    "updated": 0, "link": None, "locked": False,
                    "points": [[x - xs[0], y - ys[0]]
                               for x, y in zip(xs, ys)],
                    "pressures": ([float(p) for p in pressures]
                                  if pressures else []),
                    "simulatePressure": not pressures,
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
    validated = False  # pending excalidraw.com open-check

    def write(self, doc: ir.Document, path: Path, fidelity: Fidelity,
              options: dict[str, Any] | None = None) -> None:
        scene = document_to_scene(doc, fidelity)
        path.write_text(json.dumps(scene, indent=2), encoding="utf-8")
