"""Nebo / MyScript Notes (.nebo) -> IR.

Container: plain zip — `rel.json` (page id list), `meta.json`,
`pages/<id>/{ink.bink, page.bdom, meta.json, style.css}`. The ink lives
in `ink.bink`, MyScript's binary ink codec, reverse-engineered by this
repo (see docs/formats/nebo.md for the byte-level spec and its
confidence markers; validated against Nebo iPad 7.4.3 / iink SDK 4.4
by overlay-diff against the app's own SVG export).

BINK v5, all little-endian, byte-packed:

  "BINK\\0" u32 version(5) u8 0 u32 1
  u32 nchannels, then per channel: str name ("X","Y","F","T"),
    4B type tag, u32 has_unit, [str unit ("mm","ms")]
  u32 layout_len + layout table (skipped; see format doc)
  u32 precision_x(1000) u32 precision_y(1000)
  u32 unk(3) u8 0 u32 nstrokes
  per stroke:
    u32 flags(0x80000000) u64 t0 (MICROSECONDS since Unix epoch)
    f32 x0 f32 y0 (mm, first point) u32 unk u16 0 u32 npoints
    i16 dx[n], i16 dy[n]  -- first differences; position_mm =
        origin + cumsum(delta) / 500   (1 unit = 2 um, empirical)
    u8 f[n]               -- force, 255 = full/no pressure sensor
  tag table: u32 0, u32 count, u8 0, then `count` records
    (record 0 has no head; the rest: u32 kind, u32 id, u32 0):
    u32 name_len, name, u32 1, u16 3, u16 span_start, u32 g,
    u8 u8 (usually 05 ff), u16 span_end, u32 stroke_idx, u32 str_len,
    [utf-8 string]
  Tag names carry tool/brush ("pen-025", "brush-0500"), styling
  (".STYLE" with CSS like "color:#RRGGBBAA;-myscript-pen-pressure-
  sensitivity: 0.57;"), grouping (HIGHLIGHT_STROKES, TEXT_STROKES)
  and recognition output (CHAR/WORD/TEXT spans, DIAGRAM JSON).

The T (ms) channel is declared but not stored per point; only the
per-stroke t0 survives. F was constant 255 in every capacitive-pen
sample seen so far. MyScript's speed/pressure-based variable-width
rendering is not reimplemented: strokes get a constant-width appearance
from the brush name (pen-025 = 0.25 mm), so `exact` fidelity is
approximate for pressure-sensitive pens [inferred].

Coordinates are millimeters, y-down, page origin top-left
(`pageExtent [0,0,210,297]` = A4). point_scale = 72/25.4.

MIT-clean: decoded from controlled samples only; no MyScript SDK code
or documentation was used.
"""
from __future__ import annotations

import json
import logging
import re
import struct
import zipfile
from itertools import accumulate
from pathlib import Path

from ... import ir

_logger = logging.getLogger(__name__)

FORMAT_ID = "nebo"

MM_TO_PT = 72.0 / 25.4
#: mm per stored delta unit (empirical: 1 unit = 2 um) [verified]
DELTA_UNITS_PER_MM = 500.0

_BRUSH_RE = re.compile(r"^(?:pen|brush)-(\d{3,4})$")
_COLOR_RE = re.compile(r"color:\s*#([0-9a-fA-F]{6})([0-9a-fA-F]{2})?")


class BinkError(ValueError):
    pass


class _Cursor:
    def __init__(self, buf: bytes):
        self.buf = buf
        self.pos = 0

    def _unpack(self, fmt: str) -> tuple:
        try:
            vals = struct.unpack_from(fmt, self.buf, self.pos)
        except struct.error as exc:
            raise BinkError(f"truncated at 0x{self.pos:x}") from exc
        self.pos += struct.calcsize(fmt)
        return vals

    def u8(self) -> int:
        return self._unpack("<B")[0]

    def u16(self) -> int:
        return self._unpack("<H")[0]

    def u32(self) -> int:
        return self._unpack("<I")[0]

    def u64(self) -> int:
        return self._unpack("<Q")[0]

    def f32(self) -> float:
        return self._unpack("<f")[0]

    def raw(self, n: int) -> bytes:
        if self.pos + n > len(self.buf):
            raise BinkError(f"truncated at 0x{self.pos:x}")
        v = self.buf[self.pos:self.pos + n]
        self.pos += n
        return v

    def string(self, limit: int = 4096) -> str:
        n = self.u32()
        if n > limit:
            raise BinkError(f"implausible string length {n} at 0x{self.pos - 4:x}")
        return self.raw(n).decode("utf-8", "replace")


def parse_bink(data: bytes) -> dict:
    """Parse one ink.bink blob into {"strokes": [...], "tags": [...]}.

    Strokes: x/y in mm, f 0-255, t0 in us since epoch.
    Tags: {"name", "stroke", "span", "text"} — tag-table records.
    """
    if data[:5] != b"BINK\x00":
        raise BinkError("bad magic")
    c = _Cursor(data)
    c.pos = 5
    version = c.u32()
    if version != 5:
        _logger.warning("BINK version %d (spec derived from v5)", version)
    c.u8()
    c.u32()
    channels = []
    for _ in range(c.u32()):
        name = c.string(64)
        c.raw(4)  # type tag
        unit = c.string(64) if c.u32() else None
        channels.append((name, unit))
    c.raw(c.u32())  # per-channel layout table
    precision = (c.u32(), c.u32())
    c.u32()
    c.u8()
    nstrokes = c.u32()

    strokes = []
    for rec_idx in range(nstrokes):
        flags = c.u32()
        if flags == 0xFFFFFFFF:
            # Tombstone: an erased stroke leaves a single -1 word in the
            # record stream and still counts toward nstrokes (seen in
            # Apple-Pencil pages from Nebo iPad 7.4.3).
            continue
        if flags != 0x80000000:
            _logger.warning("unexpected stroke flags 0x%08x", flags)
        t0_us = c.u64()
        x0, y0 = c.f32(), c.f32()
        c.u32()  # constant 0x0c4910b9, meaning unknown
        c.u16()
        n = c.u32()
        if not 0 < n < 1_000_000:
            raise BinkError(f"implausible point count {n}")
        dx = struct.unpack_from(f"<{n}h", c.raw(2 * n))
        dy = struct.unpack_from(f"<{n}h", c.raw(2 * n))
        force = list(c.raw(n))
        strokes.append({
            "rec": rec_idx,  # tag stroke indices count tombstones too
            "t0_us": t0_us,
            "x": [x0 + d / DELTA_UNITS_PER_MM for d in accumulate(dx)],
            "y": [y0 + d / DELTA_UNITS_PER_MM for d in accumulate(dy)],
            "f": force,
        })

    tags = []
    try:
        c.u32()
        count = c.u32()
        c.u8()
        for i in range(count):
            if i > 0:
                c.u32()  # record kind
                c.u32()  # record id
                c.u32()
            name = c.string(256)
            groups = []
            for _ in range(c.u32()):  # span-group count (1 in most records)
                c.u16()
                span_start = c.u16()
                c.u32()  # g — matches stroke_idx except on page-level tags
                c.raw(2)  # usually 05 ff
                span_end = c.u16()
                stroke_idx = c.u32()
                groups.append((stroke_idx, (span_start, span_end)))
            text = c.string(65536)
            for stroke_idx, span in groups:
                tags.append({"name": name, "stroke": stroke_idx,
                             "span": span, "text": text})
    except BinkError as exc:
        # Geometry is already decoded; styling degrades to defaults.
        _logger.warning("BINK tag table parse stopped: %s", exc)

    return {"version": version, "channels": channels,
            "precision": precision, "strokes": strokes, "tags": tags}


def _style_of(css: str) -> dict:
    """Pick the fields we understand out of a .STYLE string."""
    out: dict = {}
    m = _COLOR_RE.search(css)
    if m:
        rgb = int(m.group(1), 16)
        out["color"] = ir.Color(((rgb >> 16) & 255) / 255.0,
                                ((rgb >> 8) & 255) / 255.0,
                                (rgb & 255) / 255.0)
        out["alpha"] = int(m.group(2), 16) / 255.0 if m.group(2) else 1.0
    m = re.search(r"-myscript-pen-pressure-sensitivity:\s*([\d.]+)", css)
    if m:
        out["pressure_sensitivity"] = float(m.group(1))
    return out


def _ir_stroke(raw: dict, tag_names: list[str], style: dict,
               brush: str | None) -> ir.Stroke:
    highlight = "HIGHLIGHT_STROKES" in tag_names
    width_mm = None
    if brush:
        m = _BRUSH_RE.match(brush)
        if m:
            width_mm = int(m.group(1)) / 100.0
    if width_mm is None:
        width_mm = 5.0 if highlight else 0.25
    color = style.get("color", ir.Color(0.0, 0.0, 0.0))
    alpha = style.get("alpha", 1.0)
    family = ir.ToolFamily.HIGHLIGHTER if highlight else ir.ToolFamily.PEN
    return ir.Stroke(
        x=raw["x"], y=raw["y"],
        tool=ir.ToolRef(
            family=family,
            native=ir.NativeTool(FORMAT_ID, brush or "pen", {
                "tags": tag_names,
                "pressure_sensitivity": style.get("pressure_sensitivity"),
            }),
        ),
        color=color,
        channels={ir.Channel.PRESSURE: [f / 255.0 for f in raw["f"]]},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT,
            width=width_mm,
            color=color,
            opacity=alpha,
            cap=ir.LineCap.ROUND,
            underlay=highlight,
            blend=ir.BlendMode.DARKEN if highlight else ir.BlendMode.NORMAL,
        ),
        extra={FORMAT_ID: {"t0_us": raw["t0_us"], "brush": brush,
                           "tags": tag_names}},
    )


def _page_from_zip(zf: zipfile.ZipFile, page_id: str) -> ir.Page:
    try:
        meta = json.loads(zf.read(f"pages/{page_id}/meta.json"))
    except KeyError:
        meta = {}
    extent = meta.get("pageExtent") or [0, 0, 210, 297]
    bounds = ir.Rect(float(extent[0]), float(extent[1]),
                     float(extent[2]), float(extent[3]))

    strokes: list[ir.Stroke] = []
    try:
        bink = parse_bink(zf.read(f"pages/{page_id}/ink.bink"))
    except KeyError:
        bink = None
    except BinkError as exc:
        _logger.warning("page %s: unreadable ink.bink (%s)", page_id, exc)
        bink = None
    if bink:
        by_stroke: dict[int, list[dict]] = {}
        for tag in bink["tags"]:
            by_stroke.setdefault(tag["stroke"], []).append(tag)
        for idx, raw in enumerate(bink["strokes"]):
            tags = by_stroke.get(raw.get("rec", idx), [])
            names = [t["name"] for t in tags]
            style: dict = {}
            for t in tags:
                if t["name"] == ".STYLE" and t["text"]:
                    style.update(_style_of(t["text"].strip('"')))
            brush = next((n for n in names if _BRUSH_RE.match(n)), None)
            strokes.append(_ir_stroke(raw, names, style, brush))

    return ir.Page(
        bounds=bounds,
        point_scale=MM_TO_PT,
        layers=[ir.Layer(strokes=strokes)],
        extra={FORMAT_ID: {"page_id": page_id}},
    )


class NeboReader:
    format_id = FORMAT_ID
    extensions = (".nebo",)

    def detect(self, path: Path) -> bool:
        try:
            if not zipfile.is_zipfile(path):
                return False
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
                return "rel.json" in names and any(
                    n.endswith("/ink.bink") or n.endswith(".bdom")
                    for n in names)
        except (OSError, zipfile.BadZipFile):
            return False

    def read(self, path: Path) -> ir.Document:
        with zipfile.ZipFile(path) as zf:
            try:
                rel = json.loads(zf.read("rel.json"))
                page_ids = list(rel.get("pages", {}))
            except (KeyError, json.JSONDecodeError):
                page_ids = sorted({n.split("/")[1] for n in zf.namelist()
                                   if n.startswith("pages/")})
            try:
                meta = json.loads(zf.read("meta.json"))
            except (KeyError, json.JSONDecodeError):
                meta = {}
            pages = [_page_from_zip(zf, pid) for pid in page_ids]
        return ir.Document(
            format_id=FORMAT_ID,
            title=str(meta.get("pageTitle") or path.stem),
            pages=pages,
            metadata={"application_version": meta.get("Application_Version"),
                      "document_version": meta.get("Document_Version")},
        )
