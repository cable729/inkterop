"""Generic SVG ink -> IR.

SVG is a *rendering* format, not an ink format; this reader ingests the
bounded subset that maps cleanly onto strokes, plus everything our own
SVG writer (`render/svg.py`) emits, so `SvgWriter` output round-trips.

Scope [verified against SVG 1.1 spec text + hand-made fixtures]:
- `<path>` with M/m L/l H/h V/v C/c Q/q Z/z (cubics/quadratics are
  flattened with a fixed 16-segment subdivision); `<polyline>`, `<line>`.
- Presentation attributes and inline `style=`: stroke, stroke-width,
  stroke-opacity, fill, fill-opacity, opacity, stroke-linecap, color.
- `transform`: translate/scale/matrix/rotate, flattened through a stack
  (rotate composed as a matrix); skewX/skewY are log-skipped.
- Nested `<svg x= y=>` treated as translate(x, y).
- `<text>` -> ir.TextBlock.

Ignored (by design): `<defs>`, `<clipPath>`, CSS `<style>` blocks,
`<use>`, gradients/patterns/masks/filters, `<image>`, A(rc) or any other
unsupported path command (the whole path is skipped with a warning).
Shapes with fill but no stroke and no data-rmi-* attributes are skipped:
they are page decorations, not ink [inferred].

Units [inferred]: page bounds come from the root `viewBox` (fallback:
width/height, then content extent); `point_scale` = (root width in pt) /
(viewBox width), where the width unit converts as pt=1, px/unitless=0.75
(CSS 96dpi), in=72, mm=72/25.4, cm=72/2.54, pc=12.

Round-tripping render/svg.py output [verified by test]:
- `data-rmi-tool` -> ir.ToolFamily, `data-rmi-pressure` -> PRESSURE.
- STROKED_CONSTANT strokes are plain stroked paths; the WIDTH channel is
  restored from `data-rmi-width` re-scaled so its first value equals the
  rendered stroke-width (the writer's own scale is not stored) [inferred].
- STROKED_VARIABLE strokes were tessellated into a closed filled outline:
  n forward points, an optional 7-point round end-cap fan, n reversed
  points, an optional 7-point start-cap fan. With n taken from the
  data-rmi channel length the centerline comes back as forward/reverse
  midpoints and per-point widths as forward/reverse distances.
- All-coincident strokes became `<circle>`; the center is replicated to
  the channel length.
- `<g data-rmi-layer>` -> ir.Layer; `<g data-rmi-template>` -> skipped
  content, background kind only (pitch/params are not embedded).
"""
from __future__ import annotations

import gzip
import logging
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from ... import ir

_logger = logging.getLogger(__name__)

FORMAT_ID = "svg"

CURVE_SEGMENTS = 16  # fixed flattening subdivision for C/Q
CAP_FAN_INTERIOR = 7  # render/svg.py CAP_FAN_SEGMENTS - 1 interior points

UNIT_TO_PT = {
    "pt": 1.0, "px": 0.75, "": 0.75,
    "in": 72.0, "mm": 72.0 / 25.4, "cm": 72.0 / 2.54, "pc": 12.0,
}

_PATH_COMMANDS = set("MmLlHhVvCcQqZz")

# --- gzip-aware IO -----------------------------------------------------------


def sniff(path: Path, n: int) -> bytes:
    """First n decompressed bytes (transparently gunzips .svgz)."""
    try:
        with open(path, "rb") as f:
            head = f.read(2)
            if head == b"\x1f\x8b":
                f.seek(0)
                with gzip.open(f) as gz:
                    return gz.read(n)
            return head + f.read(n - 2)
    except OSError:
        return b""


def read_bytes(path: Path) -> bytes:
    data = Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data


# --- 2D affine matrices (a, b, c, d, e, f) as in SVG ------------------------

Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mat_mul(m: Matrix, n: Matrix) -> Matrix:
    """m . n (apply n first, then m)."""
    return (
        m[0] * n[0] + m[2] * n[1],
        m[1] * n[0] + m[3] * n[1],
        m[0] * n[2] + m[2] * n[3],
        m[1] * n[2] + m[3] * n[3],
        m[0] * n[4] + m[2] * n[5] + m[4],
        m[1] * n[4] + m[3] * n[5] + m[5],
    )


def mat_apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def mat_scale(m: Matrix) -> float:
    """Average length scale factor: sqrt(|det|)."""
    return math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))


_TRANSFORM_RE = re.compile(r"(\w+)\s*\(([^)]*)\)")
_NUM_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def parse_transform(text: str) -> Matrix:
    """Flatten a transform list into one matrix (skew*: log-skipped)."""
    m = IDENTITY
    for name, argstr in _TRANSFORM_RE.findall(text or ""):
        args = [float(v) for v in _NUM_RE.findall(argstr)]
        if name == "matrix" and len(args) == 6:
            t = tuple(args)
        elif name == "translate" and args:
            t = (1.0, 0.0, 0.0, 1.0, args[0], args[1] if len(args) > 1 else 0.0)
        elif name == "scale" and args:
            sx = args[0]
            sy = args[1] if len(args) > 1 else sx
            t = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate" and args:
            a = math.radians(args[0])
            cos_a, sin_a = math.cos(a), math.sin(a)
            t = (cos_a, sin_a, -sin_a, cos_a, 0.0, 0.0)
            if len(args) >= 3:
                cx, cy = args[1], args[2]
                t = mat_mul(mat_mul(
                    (1.0, 0.0, 0.0, 1.0, cx, cy), t),
                    (1.0, 0.0, 0.0, 1.0, -cx, -cy))
        else:
            _logger.warning("svg: skipping unsupported transform %r", name)
            continue
        m = mat_mul(m, t)
    return m


# --- path data ---------------------------------------------------------------


class UnsupportedPath(ValueError):
    """Path uses a command outside the supported subset (e.g. A)."""


def _bezier(ctrl: list[tuple[float, float]], t: float) -> tuple[float, float]:
    """De Casteljau evaluation for quadratic/cubic control polygons."""
    pts = ctrl
    while len(pts) > 1:
        pts = [((1 - t) * ax + t * bx, (1 - t) * ay + t * by)
               for (ax, ay), (bx, by) in zip(pts, pts[1:])]
    return pts[0]


def _tokenize_path(d: str) -> list[str | float]:
    out: list[str | float] = []
    for tok in re.finditer(r"[MmLlHhVvCcQqZzAaSsTt]|" + _NUM_RE.pattern, d):
        s = tok.group(0)
        out.append(s if s.isalpha() else float(s))
    return out


def parse_path(d: str) -> list[tuple[list[tuple[float, float]], bool]]:
    """Path data -> [(points, closed)] subpaths, curves flattened.

    Supports M/m L/l H/h V/v C/c Q/q Z/z; raises UnsupportedPath on
    anything else (A/S/T...). Implicit command repetition per SVG spec
    (an M's extra pairs are linetos).
    """
    tokens = _tokenize_path(d)
    subpaths: list[tuple[list[tuple[float, float]], bool]] = []
    pts: list[tuple[float, float]] = []
    cx = cy = sx = sy = 0.0
    cmd = ""
    i = 0

    def flush(closed: bool) -> None:
        nonlocal pts
        if pts:
            subpaths.append((pts, closed))
        pts = []

    def take(n: int) -> list[float]:
        nonlocal i
        if i + n > len(tokens) or any(
                isinstance(t, str) for t in tokens[i:i + n]):
            raise UnsupportedPath(f"malformed path data near token {i}")
        vals = tokens[i:i + n]
        i += n
        return vals  # type: ignore[return-value]

    while i < len(tokens):
        tok = tokens[i]
        if isinstance(tok, str):
            if tok not in _PATH_COMMANDS:
                raise UnsupportedPath(f"unsupported path command {tok!r}")
            cmd = tok
            i += 1
            if cmd in "Zz":
                if pts:
                    cx, cy = sx, sy
                flush(closed=True)
                continue
        elif not cmd:
            raise UnsupportedPath("path data does not start with a command")

        rel = cmd.islower()
        op = cmd.upper()
        if op == "Z":
            raise UnsupportedPath("coordinates after Z without a command")
        if op != "M" and not pts:
            pts = [(cx, cy)]  # subpath resumed after Z
        if op == "M":
            x, y = take(2)
            if rel:
                x, y = cx + x, cy + y
            flush(closed=False)
            cx, cy, sx, sy = x, y, x, y
            pts = [(x, y)]
            cmd = "l" if rel else "L"  # subsequent pairs are linetos
        elif op == "L":
            x, y = take(2)
            if rel:
                x, y = cx + x, cy + y
            cx, cy = x, y
            pts.append((x, y))
        elif op == "H":
            (x,) = take(1)
            cx = cx + x if rel else x
            pts.append((cx, cy))
        elif op == "V":
            (y,) = take(1)
            cy = cy + y if rel else y
            pts.append((cx, cy))
        elif op in ("C", "Q"):
            n = 6 if op == "C" else 4
            vals = take(n)
            ctrl = [(cx, cy)]
            for j in range(0, n, 2):
                x, y = vals[j], vals[j + 1]
                if rel:
                    x, y = cx + x, cy + y
                ctrl.append((x, y))
            for k in range(1, CURVE_SEGMENTS + 1):
                pts.append(_bezier(ctrl, k / CURVE_SEGMENTS))
            cx, cy = ctrl[-1]
    flush(closed=False)
    return subpaths


# --- style + color -----------------------------------------------------------

_STYLE_KEYS = ("stroke", "fill", "stroke-width", "stroke-opacity",
               "fill-opacity", "opacity", "stroke-linecap", "color")

_NAMED_COLORS = {
    "black": (0.0, 0.0, 0.0), "white": (1.0, 1.0, 1.0),
    "red": (1.0, 0.0, 0.0), "lime": (0.0, 1.0, 0.0),
    "green": (0.0, 0.5, 0.0), "blue": (0.0, 0.0, 1.0),
    "yellow": (1.0, 1.0, 0.0), "cyan": (0.0, 1.0, 1.0),
    "aqua": (0.0, 1.0, 1.0), "magenta": (1.0, 0.0, 1.0),
    "fuchsia": (1.0, 0.0, 1.0), "gray": (0.5, 0.5, 0.5),
    "grey": (0.5, 0.5, 0.5), "silver": (0.75, 0.75, 0.75),
    "orange": (1.0, 0.647, 0.0), "purple": (0.5, 0.0, 0.5),
    "brown": (0.647, 0.165, 0.165),
}


def parse_color(text: str | None, current: ir.Color | None = None
                ) -> ir.Color | None:
    """CSS color -> ir.Color; None for none/unpaintable/unparseable."""
    if text is None:
        return None
    s = text.strip()
    low = s.lower()
    if low in ("none", "transparent"):
        return None
    if low == "currentcolor":
        return current or ir.Color(0.0, 0.0, 0.0)
    if s.startswith("#"):
        h = s[1:]
        try:
            if len(h) == 3:
                return ir.Color(*(int(c * 2, 16) / 255.0 for c in h))
            if len(h) == 6:
                return ir.Color(*(int(h[j:j + 2], 16) / 255.0
                                  for j in (0, 2, 4)))
            if len(h) == 8:  # #rrggbbaa (CSS4; rare but cheap)
                r, g, b, a = (int(h[j:j + 2], 16) / 255.0
                              for j in (0, 2, 4, 6))
                return ir.Color(r, g, b, a)
        except ValueError:
            return None
        return None
    if low.startswith("rgb"):
        nums = _NUM_RE.findall(s)
        if len(nums) >= 3:
            vals = []
            for n in nums[:3]:
                v = float(n)
                vals.append(v / 100.0 if "%" in s else v / 255.0)
            return ir.Color(*(min(1.0, max(0.0, v)) for v in vals))
        return None
    if low in _NAMED_COLORS:
        return ir.Color(*_NAMED_COLORS[low])
    return None


def _style_of(el: ET.Element, inherited: dict[str, str]) -> dict[str, str]:
    st = dict(inherited)
    for key in _STYLE_KEYS:
        if key in el.attrib:
            st[key] = el.attrib[key]
    for decl in (el.get("style") or "").split(";"):
        if ":" in decl:
            k, _, v = decl.partition(":")
            k = k.strip()
            if k in _STYLE_KEYS or k == "mix-blend-mode":
                st[k] = v.strip()
    return st


def _float_of(st: dict[str, str], key: str, default: float) -> float:
    try:
        m = _NUM_RE.search(st.get(key, ""))
        return float(m.group(0)) if m else default
    except ValueError:
        return default


def parse_length(text: str | None) -> tuple[float, str] | None:
    """'900px' -> (900.0, 'px'); None when absent/unparseable."""
    if not text:
        return None
    m = re.match(r"\s*([-+]?[\d.]+(?:[eE][-+]?\d+)?)\s*([a-z%]*)", text)
    if not m:
        return None
    try:
        return float(m.group(1)), m.group(2)
    except ValueError:
        return None


# --- element walking ---------------------------------------------------------

def _local(tag: object) -> str:
    if not isinstance(tag, str):  # comments/PIs
        return ""
    return tag.rsplit("}", 1)[-1]


_SKIP_TAGS = {
    "defs", "clipPath", "style", "use", "symbol", "pattern", "mask",
    "marker", "metadata", "title", "desc", "linearGradient",
    "radialGradient", "filter", "script", "image", "foreignObject",
}


def _floats_attr(el: ET.Element, name: str) -> list[float] | None:
    raw = el.get(name)
    if raw is None:
        return None
    try:
        return [float(v) for v in raw.split()]
    except ValueError:
        return None


def _reconstruct_outline(pts: list[tuple[float, float]], n: int
                         ) -> tuple[list[float], list[float],
                                    list[float]] | None:
    """Invert render/svg.py outline_polygon: xs, ys, per-point widths."""
    if n < 1:
        return None
    if len(pts) == 2 * n:
        fan = 0
    elif len(pts) == 2 * n + 2 * CAP_FAN_INTERIOR:
        fan = CAP_FAN_INTERIOR
    else:
        return None
    fwd = pts[:n]
    rev = pts[n + fan:n + fan + n][::-1]
    xs = [(a[0] + b[0]) / 2 for a, b in zip(fwd, rev)]
    ys = [(a[1] + b[1]) / 2 for a, b in zip(fwd, rev)]
    widths = [math.hypot(a[0] - b[0], a[1] - b[1])
              for a, b in zip(fwd, rev)]
    return xs, ys, widths


class Walker:
    """Collects strokes/text from an element tree into IR layers.

    Subclass hooks: `skip(el)` prunes subtrees, `tool_for(el)` supplies
    the ToolRef (default: data-rmi-tool attr or UNKNOWN).
    """

    def __init__(self) -> None:
        self.layers: list[ir.Layer] = []
        self.template_kind: str | None = None
        self._default_layer: ir.Layer | None = None

    # -- hooks --
    def skip(self, el: ET.Element) -> bool:
        return False

    def tool_for(self, el: ET.Element) -> ir.ToolRef:
        raw = el.get("data-rmi-tool")
        try:
            family = ir.ToolFamily(raw) if raw else ir.ToolFamily.UNKNOWN
        except ValueError:
            family = ir.ToolFamily.UNKNOWN
        return ir.ToolRef(family=family)

    # -- accumulation --
    def _target(self, layer: ir.Layer | None) -> ir.Layer:
        if layer is not None:
            return layer
        if self._default_layer is None:
            self._default_layer = ir.Layer()
            self.layers.append(self._default_layer)
        return self._default_layer

    def walk(self, el: ET.Element, mat: Matrix = IDENTITY,
             style: dict[str, str] | None = None,
             layer: ir.Layer | None = None) -> None:
        tag = _local(el.tag)
        if not tag or tag in _SKIP_TAGS or self.skip(el):
            return
        if "data-rmi-template" in el.attrib:  # our writer's page template
            self.template_kind = el.get("data-rmi-template")
            return

        style = _style_of(el, style or {})
        if el.get("transform"):
            mat = mat_mul(mat, parse_transform(el.get("transform", "")))

        if tag in ("g", "a", "switch"):
            if "data-rmi-layer" in el.attrib:
                layer = ir.Layer(name=el.get("data-rmi-layer", ""))
                self.layers.append(layer)
            for child in el:
                self.walk(child, mat, style, layer)
            return
        if tag == "svg":  # nested svg: x/y offset only [inferred]
            x = (parse_length(el.get("x")) or (0.0, ""))[0]
            y = (parse_length(el.get("y")) or (0.0, ""))[0]
            if x or y:
                mat = mat_mul(mat, (1.0, 0.0, 0.0, 1.0, x, y))
            for child in el:
                self.walk(child, mat, style, layer)
            return
        if tag == "text":
            self._add_text(el, mat, style, layer)
            return
        if tag in ("path", "polyline", "line", "circle"):
            for stroke in self._element_strokes(el, tag, mat, style):
                self._target(layer).strokes.append(stroke)

    def _add_text(self, el: ET.Element, mat: Matrix, style: dict[str, str],
                  layer: ir.Layer | None) -> None:
        text = "".join(el.itertext()).strip()
        if not text:
            return
        x, y = mat_apply(mat, float(el.get("x", 0)), float(el.get("y", 0)))
        color = parse_color(el.get("fill") or style.get("fill"))
        size = parse_length(el.get("font-size"))
        self._target(layer).texts.append(ir.TextBlock(
            x=x, y=y, text=text, color=color,
            font_size=size[0] * mat_scale(mat) if size else None,
        ))

    # -- geometry -> strokes --
    def _element_strokes(self, el: ET.Element, tag: str, mat: Matrix,
                         style: dict[str, str]) -> list[ir.Stroke]:
        has_rmi = any(a.startswith("data-rmi-") for a in el.attrib)
        current = parse_color(style.get("color"))
        stroke_color = parse_color(style.get("stroke"), current)
        fill_color = parse_color(style.get("fill", "black"), current)
        if stroke_color is None and fill_color is None:
            return []
        if stroke_color is None and not has_rmi and tag != "line":
            return []  # fill-only decoration, not ink [inferred]

        if tag == "circle":
            return self._circle_strokes(el, mat, style, fill_color, has_rmi)

        subpaths: list[tuple[list[tuple[float, float]], bool]]
        if tag == "path":
            try:
                subpaths = parse_path(el.get("d", ""))
            except UnsupportedPath as e:
                _logger.warning("svg: skipping path (%s)", e)
                return []
        elif tag == "polyline":
            nums = [float(v) for v in _NUM_RE.findall(el.get("points", ""))]
            subpaths = [(list(zip(nums[::2], nums[1::2])), False)]
        else:  # line
            subpaths = [([(float(el.get("x1", 0)), float(el.get("y1", 0))),
                          (float(el.get("x2", 0)), float(el.get("y2", 0)))],
                         False)]
            if stroke_color is None:
                return []

        out = []
        for pts, closed in subpaths:
            pts = [mat_apply(mat, x, y) for x, y in pts]
            if not pts:
                continue
            st = (self._stroked(el, pts, closed, mat, style, stroke_color)
                  if stroke_color is not None
                  else self._filled(el, pts, style, fill_color))
            if st is not None:
                out.append(st)
        return out

    def _channels(self, el: ET.Element, npts: int,
                  width_scale: float | None) -> dict[ir.Channel, list[float]]:
        """data-rmi channels that match the point count."""
        channels: dict[ir.Channel, list[float]] = {}
        pressure = _floats_attr(el, "data-rmi-pressure")
        if pressure and len(pressure) == npts:
            channels[ir.Channel.PRESSURE] = pressure
        widths = _floats_attr(el, "data-rmi-width")
        if widths and len(widths) == npts and width_scale is not None:
            channels[ir.Channel.WIDTH] = [w * width_scale for w in widths]
        return channels

    def _appearance(self, style: dict[str, str], color: ir.Color,
                    mode: ir.GeometryMode, width: float | None,
                    opacity: float) -> ir.StrokeAppearance:
        cap = style.get("stroke-linecap", "round")
        try:
            cap_enum = ir.LineCap(cap)
        except ValueError:
            cap_enum = ir.LineCap.ROUND
        blend = ir.BlendMode.NORMAL
        try:
            blend = ir.BlendMode(style.get("mix-blend-mode", "normal"))
        except ValueError:
            pass
        return ir.StrokeAppearance(mode=mode, color=color, width=width,
                                   opacity=opacity, cap=cap_enum, blend=blend)

    def _stroked(self, el: ET.Element, pts: list[tuple[float, float]],
                 closed: bool, mat: Matrix, style: dict[str, str],
                 color: ir.Color) -> ir.Stroke:
        if closed and len(pts) > 1 and pts[0] != pts[-1]:
            pts = pts + [pts[0]]
        width = _float_of(style, "stroke-width", 1.0) * mat_scale(mat)
        opacity = (_float_of(style, "stroke-opacity", 1.0)
                   * _float_of(style, "opacity", 1.0))
        raw = _floats_attr(el, "data-rmi-width")
        wscale = width / raw[0] if raw and raw[0] > 0 else None
        return ir.Stroke(
            x=[p[0] for p in pts], y=[p[1] for p in pts],
            tool=self.tool_for(el), color=color,
            channels=self._channels(el, len(pts), wscale),
            appearance=self._appearance(
                style, color, ir.GeometryMode.STROKED_CONSTANT,
                width, opacity),
        )

    def _filled(self, el: ET.Element, pts: list[tuple[float, float]],
                style: dict[str, str], color: ir.Color | None
                ) -> ir.Stroke | None:
        """Filled outline with data-rmi attrs: invert the tessellation."""
        if color is None:
            return None
        opacity = (_float_of(style, "fill-opacity", 1.0)
                   * _float_of(style, "opacity", 1.0))
        n = None
        for attr in ("data-rmi-width", "data-rmi-pressure"):
            values = _floats_attr(el, attr)
            if values:
                n = len(values)
                break
        rebuilt = _reconstruct_outline(pts, n) if n else None
        if rebuilt is not None:
            xs, ys, widths = rebuilt
            channels = self._channels(el, len(xs), None)
            channels[ir.Channel.WIDTH] = widths
            return ir.Stroke(
                x=xs, y=ys, tool=self.tool_for(el), color=color,
                channels=channels,
                appearance=self._appearance(
                    style, color, ir.GeometryMode.STROKED_VARIABLE,
                    None, opacity),
            )
        # Unknown filled shape carrying ink markers: keep the outline.
        return ir.Stroke(
            x=[p[0] for p in pts], y=[p[1] for p in pts],
            tool=self.tool_for(el), color=color,
            channels=self._channels(el, len(pts), None),
            appearance=self._appearance(
                style, color, ir.GeometryMode.FILLED_OUTLINE, None, opacity),
        )

    def _circle_strokes(self, el: ET.Element, mat: Matrix,
                        style: dict[str, str], fill: ir.Color | None,
                        has_rmi: bool) -> list[ir.Stroke]:
        """Our writer's degenerate all-coincident stroke [verified]."""
        if not has_rmi or fill is None:
            return []  # generic circles are out of scope
        cx, cy = mat_apply(mat, float(el.get("cx", 0)), float(el.get("cy", 0)))
        r = float(el.get("r", 0)) * mat_scale(mat)
        n = 1
        for attr in ("data-rmi-width", "data-rmi-pressure"):
            values = _floats_attr(el, attr)
            if values:
                n = len(values)
                break
        raw = _floats_attr(el, "data-rmi-width")
        wscale = 2 * r / max(raw) if raw and max(raw) > 0 else None
        opacity = (_float_of(style, "fill-opacity", 1.0)
                   * _float_of(style, "opacity", 1.0))
        channels = self._channels(el, n, wscale)
        if ir.Channel.WIDTH not in channels:
            channels[ir.Channel.WIDTH] = [2 * r] * n
        return [ir.Stroke(
            x=[cx] * n, y=[cy] * n, tool=self.tool_for(el), color=fill,
            channels=channels,
            appearance=self._appearance(
                style, fill, ir.GeometryMode.STROKED_VARIABLE, None, opacity),
        )]


# --- document assembly -------------------------------------------------------

def _page_geometry(root: ET.Element, layers: list[ir.Layer]
                   ) -> tuple[ir.Rect, float]:
    """Bounds from viewBox (fallback width/height, then content extent);
    point_scale from the width unit [inferred]."""
    view_box = _NUM_RE.findall(root.get("viewBox", ""))
    width = parse_length(root.get("width"))
    height = parse_length(root.get("height"))
    unit_pt = UNIT_TO_PT.get((width or (0, ""))[1], 0.75)

    if len(view_box) == 4:
        x0, y0, w, h = (float(v) for v in view_box)
        scale = width[0] * unit_pt / w if width and w > 0 else unit_pt
        return ir.Rect(x0, y0, x0 + w, y0 + h), scale
    if width and height:
        return ir.Rect(0.0, 0.0, width[0], height[0]), unit_pt
    xs = [x for ly in layers for s in ly.strokes for x in s.x]
    ys = [y for ly in layers for s in ly.strokes for y in s.y]
    if xs:
        return ir.Rect(min(min(xs), 0.0), min(min(ys), 0.0),
                       max(xs), max(ys)), unit_pt
    return ir.Rect(0.0, 0.0, 100.0, 100.0), unit_pt


def read_svg_root(root: ET.Element, title: str = "") -> ir.Document:
    walker = Walker()
    for child in root:
        walker.walk(child)
    layers = [ly for ly in walker.layers
              if ly.strokes or ly.texts] or [ir.Layer()]
    bounds, scale = _page_geometry(root, layers)
    background = (ir.TemplateBackground(kind=walker.template_kind)
                  if walker.template_kind else None)
    return ir.Document(
        format_id=FORMAT_ID,
        title=title,
        pages=[ir.Page(bounds=bounds, point_scale=scale, layers=layers,
                       background=background)],
    )


class SvgReader:
    """Generic SVG -> one-page IR document."""

    format_id = FORMAT_ID
    extensions = (".svg", ".svgz")

    def detect(self, path: Path) -> bool:
        # Any svg qualifies, including Write-flavored ones: registry
        # ordering puts WriteReader first, so it wins for those.
        return b"<svg" in sniff(path, 4096)

    def read(self, path: Path) -> ir.Document:
        root = ET.fromstring(read_bytes(path))
        return read_svg_root(root, title=Path(path).stem)
