"""W3C InkML reader + writer: the raw-fidelity flagship format.

Spec: https://www.w3.org/TR/InkML/. The writer emits a faithful-but-
pragmatic subset — standard channels (X/Y/F/OA/OE/T) plus inkterop
extension channels (W/S/A) and `annotationXML` blocks carrying tool,
appearance and page metadata for lossless round-trips. The reader also
accepts foreign InkML, including the !/'/" value-prefix encodings
(explicit / first difference / second difference) that OneNote emits.
Exact mapping + extension schema: docs/formats/inkml-mapping.md.

Trace coordinates are written in PDF points rebased to the page's
top-left corner: (x - bounds.x_min) * point_scale, rounded to 4
decimals. Page metadata annotations let the reader invert this exactly.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from xml.sax.saxutils import escape, quoteattr

from .. import ir
from ..ir.defaults import restyled
from .base import Fidelity

FORMAT_ID = "inkml"
INKML_NS = "http://www.w3.org/2003/InkML"
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"

#: annotationXML `type` markers for the inkterop extension schema.
ANNOT_BRUSH = "inkterop"
ANNOT_PAGE = "inkterop-page"
ANNOT_LAYER = "inkterop-layer"

#: Canonical channel order after X/Y: (IR channel, InkML name, extra attrs).
#: F/OA/OE/T are standard InkML; W/S/A are inkterop extensions (W is
#: spec-reserved for stroke width — compatible; our S/A usage is not).
_CHANNEL_DEFS: list[tuple[ir.Channel, str, str]] = [
    (ir.Channel.PRESSURE, "F", ' min="0" max="1"'),
    (ir.Channel.TILT_AZIMUTH, "OA", ' units="rad"'),
    (ir.Channel.TILT_ALTITUDE, "OE", ' units="rad"'),
    (ir.Channel.TIMESTAMP, "T", ' units="s"'),
    (ir.Channel.WIDTH, "W", ' units="pt"'),
    (ir.Channel.SPEED, "S", ""),
    (ir.Channel.ALPHA, "A", ' min="0" max="1"'),
]
_NAME_TO_CHANNEL = {name: ch for ch, name, _ in _CHANNEL_DEFS}


def _fmt4(v: float) -> str:
    """Trace values: rounded to 4 decimals, trailing zeros trimmed."""
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _fnum(v: float) -> str:
    """Metadata values: shortest exact text (float(repr(v)) == v)."""
    return repr(float(v))


def _color_hex(c: ir.Color) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        round(c.r * 255), round(c.g * 255), round(c.b * 255)
    )


def _hex_to_color(s: str) -> ir.Color:
    s = s.lstrip("#")
    if len(s) < 6:
        return ir.Color(0.0, 0.0, 0.0)
    r, g, b = (int(s[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return ir.Color(r, g, b)


# --- writer -----------------------------------------------------------------

def _channel_key(stroke: ir.Stroke) -> tuple[str, ...]:
    return tuple(name for ch, name, _ in _CHANNEL_DEFS if ch in stroke.channels)


def _context_xml(cid: str, key: tuple[str, ...]) -> str:
    chans = ['<channel name="X" type="decimal"/>',
             '<channel name="Y" type="decimal"/>']
    for _, name, attrs in _CHANNEL_DEFS:
        if name in key:
            chans.append(f'<channel name="{name}" type="decimal"{attrs}/>')
    return (f'<context xml:id="{cid}"><traceFormat>'
            f'{"".join(chans)}</traceFormat></context>')


def _color_attrs(c: ir.Color) -> str:
    return (f'r="{_fnum(c.r)}" g="{_fnum(c.g)}" '
            f'b="{_fnum(c.b)}" a="{_fnum(c.a)}"')


def _brush_body(stroke: ir.Stroke, point_scale: float) -> str:
    """Brush element body; also serves as the dedup key for brush ids."""
    app = stroke.appearance
    render_color = app.color if app else stroke.color
    opacity = app.opacity if app else 1.0
    parts = [
        f'<brushProperty name="color" value="{_color_hex(render_color)}"/>',
        f'<brushProperty name="transparency" value="{_fmt4(1.0 - opacity)}"/>',
    ]
    if app is not None and app.width is not None:
        parts.append(f'<brushProperty name="width" '
                     f'value="{_fmt4(app.width * point_scale)}"/>')

    ann = [f'<annotationXML type="{ANNOT_BRUSH}">']
    tool = stroke.tool
    if tool.native is not None:
        n = tool.native
        kind = "int" if isinstance(n.tool_id, int) else "str"
        ann.append(
            f'<tool family="{tool.family.value}">'
            f'<native formatId={quoteattr(n.format_id)} '
            f'toolId={quoteattr(str(n.tool_id))} toolIdKind="{kind}" '
            f'params={quoteattr(json.dumps(n.params, sort_keys=True))}/>'
            f'</tool>')
    else:
        ann.append(f'<tool family="{tool.family.value}"/>')
    ann.append(f'<color {_color_attrs(stroke.color)}/>')
    if app is not None:
        width_attr = f' width="{_fnum(app.width)}"' if app.width is not None else ""
        ann.append(
            f'<appearance mode="{app.mode.value}"{width_attr} '
            f'opacity="{_fnum(app.opacity)}" blend="{app.blend.value}" '
            f'cap="{app.cap.value}" join="{app.join.value}" '
            f'underlay="{"true" if app.underlay else "false"}">'
            f'<renderColor {_color_attrs(app.color)}/>'
            f'</appearance>')
    ann.append('</annotationXML>')
    return "".join(parts) + "".join(ann)


def _trace_text(stroke: ir.Stroke, page: ir.Page) -> str:
    scale, x0, y0 = page.point_scale, page.bounds.x_min, page.bounds.y_min
    present = [(ch, ch is ir.Channel.WIDTH)
               for ch, _, _ in _CHANNEL_DEFS if ch in stroke.channels]
    points = []
    for i in range(len(stroke.x)):
        vals = [_fmt4((stroke.x[i] - x0) * scale),
                _fmt4((stroke.y[i] - y0) * scale)]
        for ch, is_width in present:
            v = stroke.channels[ch][i]
            vals.append(_fmt4(v * scale if is_width else v))
        points.append(" ".join(vals))
    return ", ".join(points)


def document_to_inkml(doc: ir.Document,
                      fidelity: Fidelity = Fidelity.EXACT) -> str:
    ctx_ids: dict[tuple[str, ...], str] = {}
    brush_ids: dict[str, str] = {}

    body: list[str] = []
    for pi, page in enumerate(doc.pages):
        b = page.bounds
        body.append(f'<traceGroup xml:id="page{pi}">')
        body.append(
            f'<annotationXML type="{ANNOT_PAGE}">'
            f'<page xMin="{_fnum(b.x_min)}" yMin="{_fnum(b.y_min)}" '
            f'xMax="{_fnum(b.x_max)}" yMax="{_fnum(b.y_max)}" '
            f'pointScale="{_fnum(page.point_scale)}" '
            f'orientation="{doc.orientation}"/></annotationXML>')
        for layer in (page.layers or [ir.Layer()]):
            body.append('<traceGroup>')
            body.append(
                f'<annotationXML type="{ANNOT_LAYER}">'
                f'<layer name={quoteattr(layer.name)} '
                f'visible="{"true" if layer.visible else "false"}"/>'
                f'</annotationXML>')
            for stroke in layer.strokes:
                if not stroke.x:
                    continue
                if fidelity is Fidelity.NATIVE:
                    stroke = restyled(stroke)
                key = _channel_key(stroke)
                cid = ctx_ids.setdefault(key, f"ctx{len(ctx_ids)}")
                bbody = _brush_body(stroke, page.point_scale)
                bid = brush_ids.setdefault(bbody, f"br{len(brush_ids)}")
                body.append(f'<trace contextRef="#{cid}" brushRef="#{bid}">'
                            f'{_trace_text(stroke, page)}</trace>')
            body.append('</traceGroup>')
        body.append('</traceGroup>')

    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<ink xmlns="{INKML_NS}">']
    if doc.title:
        out.append(f'<annotation type="title">{escape(doc.title)}</annotation>')
    out.append('<definitions>')
    out.extend(_context_xml(cid, key) for key, cid in ctx_ids.items())
    out.extend(f'<brush xml:id="{bid}">{bbody}</brush>'
               for bbody, bid in brush_ids.items())
    out.append('</definitions>')
    out.extend(body)
    out.append('</ink>')
    return "\n".join(out) + "\n"


class InkmlWriter:
    format_id = FORMAT_ID
    extensions = (".inkml", ".ink")
    validated = True  # open standard; round-trip covered in tests

    def write(self, doc: ir.Document, path: Path, fidelity: Fidelity,
              options: dict[str, Any] | None = None) -> None:
        # EXACT and RAW are identical here: InkML holds both the raw
        # channels and the appearance annotations in one file.
        Path(path).write_text(document_to_inkml(doc, fidelity),
                              encoding="utf-8")


# --- reader -----------------------------------------------------------------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _elem_id(el: ElementTree.Element) -> str:
    return el.get(XML_ID) or el.get("id") or ""


_TOKEN_RE = re.compile(r"([!'\"])?\s*(-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")


def _decode_trace(text: str) -> list[list[float]]:
    """Decode trace text to per-point value rows.

    Applies the InkML value-prefix scheme: ! explicit, ' first
    difference (velocity), " second difference (acceleration). Per the
    spec, a prefix sets the mode for that channel and the mode persists
    for following unprefixed values until changed.
    """
    mode: list[int] = []  # per channel index: 0 explicit, 1 vel, 2 accel
    pos: list[float] = []
    vel: list[float] = []
    rows: list[list[float]] = []
    for point in text.split(","):
        point = point.strip()
        if not point:
            continue
        row: list[float] = []
        for i, (prefix, num) in enumerate(_TOKEN_RE.findall(point)):
            v = float(num)
            while len(mode) <= i:
                mode.append(0)
                pos.append(0.0)
                vel.append(0.0)
            if prefix == "'":
                mode[i] = 1
            elif prefix == '"':
                mode[i] = 2
            elif prefix == "!":
                mode[i] = 0
            if mode[i] == 0:
                vel[i] = v - pos[i]
                pos[i] = v
            elif mode[i] == 1:
                vel[i] = v
                pos[i] += v
            else:
                vel[i] += v
                pos[i] += vel[i]
            row.append(pos[i])
        if row:
            rows.append(row)
    return rows


def _parse_contexts(root: ElementTree.Element) -> dict[str, list[str]]:
    """Map context/traceFormat xml:id -> ordered channel names."""
    ctxs: dict[str, list[str]] = {}
    for el in root.iter():
        if _local(el.tag) in ("context", "traceFormat"):
            cid = _elem_id(el)
            if not cid:
                continue
            names = [c.get("name", "") for c in el.iter()
                     if _local(c.tag) == "channel"]
            if names:
                ctxs[cid] = names
    return ctxs


def _enum(cls, value: str | None, default):
    try:
        return cls(value)
    except ValueError:
        return default


def _color_from_el(el: ElementTree.Element) -> ir.Color:
    return ir.Color(float(el.get("r", 0)), float(el.get("g", 0)),
                    float(el.get("b", 0)), float(el.get("a", 1)))


class _Brush:
    __slots__ = ("tool", "color", "appearance")

    def __init__(self, tool: ir.ToolRef, color: ir.Color,
                 appearance: ir.StrokeAppearance | None):
        self.tool = tool
        self.color = color
        self.appearance = appearance


_DEFAULT_BRUSH = _Brush(ir.ToolRef(ir.ToolFamily.PEN), ir.Color(0, 0, 0), None)


def _parse_brush(el: ElementTree.Element) -> _Brush:
    props = {p.get("name"): p.get("value", "") for p in el
             if _local(p.tag) == "brushProperty"}
    tool_el = color_el = app_el = None
    for c in el.iter():
        name = _local(c.tag)
        if name == "tool" and tool_el is None:
            tool_el = c
        elif name == "color" and color_el is None:
            color_el = c
        elif name == "appearance" and app_el is None:
            app_el = c

    if tool_el is None:  # foreign brush: no inkterop annotation
        return _Brush(ir.ToolRef(ir.ToolFamily.PEN),
                      _hex_to_color(props.get("color", "#000000")), None)

    family = _enum(ir.ToolFamily, tool_el.get("family"),
                   ir.ToolFamily.UNKNOWN)
    native = None
    nat_el = next((c for c in tool_el if _local(c.tag) == "native"), None)
    if nat_el is not None:
        tool_id: str | int = nat_el.get("toolId", "")
        if nat_el.get("toolIdKind") == "int":
            tool_id = int(tool_id)
        native = ir.NativeTool(nat_el.get("formatId", ""), tool_id,
                               json.loads(nat_el.get("params") or "{}"))

    color = (_color_from_el(color_el) if color_el is not None
             else _hex_to_color(props.get("color", "#000000")))
    appearance = None
    if app_el is not None:
        rc_el = next((c for c in app_el if _local(c.tag) == "renderColor"),
                     None)
        width = app_el.get("width")
        appearance = ir.StrokeAppearance(
            mode=_enum(ir.GeometryMode, app_el.get("mode"),
                       ir.GeometryMode.STROKED_VARIABLE),
            color=_color_from_el(rc_el) if rc_el is not None else color,
            width=float(width) if width is not None else None,
            opacity=float(app_el.get("opacity", 1.0)),
            blend=_enum(ir.BlendMode, app_el.get("blend"),
                        ir.BlendMode.NORMAL),
            cap=_enum(ir.LineCap, app_el.get("cap"), ir.LineCap.ROUND),
            join=_enum(ir.LineCap, app_el.get("join"), ir.LineCap.ROUND),
            underlay=app_el.get("underlay") == "true",
        )
    return _Brush(ir.ToolRef(family, native), color, appearance)


def _find_annotation_child(group: ElementTree.Element,
                           child_name: str) -> ElementTree.Element | None:
    """First `child_name` element inside a direct annotationXML child."""
    for c in group:
        if _local(c.tag) == "annotationXML":
            for sub in c.iter():
                if _local(sub.tag) == child_name:
                    return sub
    return None


def _stroke_from_trace(el: ElementTree.Element, names: list[str],
                       brush: _Brush, scale: float, x0: float,
                       y0: float) -> ir.Stroke | None:
    rows = _decode_trace(el.text or "")
    rows = [row for row in rows if len(row) >= 2]
    if not rows:
        return None
    n = len(names)
    xs: list[float] = []
    ys: list[float] = []
    columns: dict[ir.Channel, list[float]] = {}
    for row in rows:
        row = row + [0.0] * (n - len(row))
        for i, name in enumerate(names[:len(row)]):
            v = row[i]
            if name == "X":
                xs.append(v / scale + x0)
            elif name == "Y":
                ys.append(v / scale + y0)
            else:
                ch = _NAME_TO_CHANNEL.get(name)
                if ch is None:
                    continue
                if ch is ir.Channel.WIDTH:
                    v /= scale
                columns.setdefault(ch, []).append(v)
    if len(xs) != len(ys) or not xs:
        return None
    return ir.Stroke(x=xs, y=ys, tool=brush.tool, color=brush.color,
                     channels=columns, appearance=brush.appearance)


def _resolve(el: ElementTree.Element, attr: str, inherited: str) -> str:
    ref = el.get(attr)
    return ref.lstrip("#") if ref else inherited


class InkmlReader:
    format_id = FORMAT_ID
    extensions = (".inkml", ".ink")

    def detect(self, path: Path) -> bool:
        try:
            with open(path, "rb") as f:
                head = f.read(2048)
        except OSError:
            return False
        return b"<ink" in head and b"InkML" in head

    def read(self, path: Path) -> ir.Document:
        root = ElementTree.fromstring(Path(path).read_bytes())
        if _local(root.tag) != "ink":
            raise ValueError(f"not an InkML document: root <{_local(root.tag)}>")
        ctxs = _parse_contexts(root)
        brushes = {bid: _parse_brush(el) for el in root.iter()
                   if _local(el.tag) == "brush" and (bid := _elem_id(el))}

        title = ""
        for c in root:
            if _local(c.tag) == "annotation" and c.get("type") == "title":
                title = c.text or ""

        pages: list[ir.Page] = []
        orientation = ""
        # Top-level traceGroups are pages; stray top-level traces form an
        # implicit page (foreign InkML).
        stray = [c for c in root if _local(c.tag) == "trace"]
        groups = [c for c in root if _local(c.tag) == "traceGroup"]
        ctx0 = _resolve(root, "contextRef", "")
        br0 = _resolve(root, "brushRef", "")
        for g in groups:
            page, page_orient = self._read_page(g, ctxs, brushes, ctx0, br0)
            pages.append(page)
            orientation = orientation or page_orient
        if stray:
            strokes = self._read_traces(stray, ctxs, brushes, ctx0, br0,
                                        1.0, 0.0, 0.0)
            pages.append(ir.Page(bounds=_extent_bounds(strokes),
                                 point_scale=1.0,
                                 layers=[ir.Layer(strokes=strokes)]))
        if not orientation:
            orientation = "portrait"
            if pages and pages[0].bounds.width > pages[0].bounds.height:
                orientation = "landscape"
        return ir.Document(format_id=FORMAT_ID, title=title,
                           orientation=orientation, pages=pages)

    def _read_traces(self, els, ctxs, brushes, ctx_id, brush_id,
                     scale, x0, y0) -> list[ir.Stroke]:
        strokes = []
        for el in els:
            names = ctxs.get(_resolve(el, "contextRef", ctx_id), ["X", "Y"])
            brush = brushes.get(_resolve(el, "brushRef", brush_id),
                                _DEFAULT_BRUSH)
            s = _stroke_from_trace(el, names, brush, scale, x0, y0)
            if s is not None:
                strokes.append(s)
        return strokes

    def _read_page(self, group, ctxs, brushes, ctx_id,
                   brush_id) -> tuple[ir.Page, str]:
        ctx_id = _resolve(group, "contextRef", ctx_id)
        brush_id = _resolve(group, "brushRef", brush_id)
        meta = _find_annotation_child(group, "page")
        if meta is not None:
            bounds = ir.Rect(float(meta.get("xMin", 0)),
                             float(meta.get("yMin", 0)),
                             float(meta.get("xMax", 0)),
                             float(meta.get("yMax", 0)))
            scale = float(meta.get("pointScale", 1.0))
            orientation = meta.get("orientation", "")
        else:
            bounds, scale, orientation = None, 1.0, ""
        x0 = bounds.x_min if bounds is not None else 0.0
        y0 = bounds.y_min if bounds is not None else 0.0

        layers: list[ir.Layer] = []
        direct_traces = []
        for c in group:
            if _local(c.tag) == "traceGroup":
                lc = _resolve(c, "contextRef", ctx_id)
                lb = _resolve(c, "brushRef", brush_id)
                traces = [t for t in c if _local(t.tag) == "trace"]
                strokes = self._read_traces(traces, ctxs, brushes, lc, lb,
                                            scale, x0, y0)
                layer_meta = _find_annotation_child(c, "layer")
                name, visible = "", True
                if layer_meta is not None:
                    name = layer_meta.get("name", "")
                    visible = layer_meta.get("visible", "true") != "false"
                layers.append(ir.Layer(strokes=strokes, name=name,
                                       visible=visible))
            elif _local(c.tag) == "trace":
                direct_traces.append(c)
        if direct_traces:
            layers.append(ir.Layer(strokes=self._read_traces(
                direct_traces, ctxs, brushes, ctx_id, brush_id,
                scale, x0, y0)))
        if bounds is None:
            bounds = _extent_bounds([s for l in layers for s in l.strokes])
        return ir.Page(bounds=bounds, point_scale=scale,
                       layers=layers), orientation


def _extent_bounds(strokes: list[ir.Stroke]) -> ir.Rect:
    xs = [v for s in strokes for v in s.x]
    ys = [v for s in strokes for v in s.y]
    if not xs:
        return ir.Rect(0.0, 0.0, 612.0, 792.0)  # US Letter fallback
    return ir.Rect(min(xs), min(ys), max(xs), max(ys))
