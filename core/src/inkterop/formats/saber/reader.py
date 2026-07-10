"""Saber (saber-notes) -> IR.

Format ([verified] against a Mac Saber export, format version 19):
`.sba` = zip containing `main.sbn2` (+ assets); `.sbn2` = a single BSON
document:

  v: int      format version (19 observed)
  z: array    pages: {w, h: page size (units), s: strokes[], q: Quill
              delta rich text, ...}
  stroke:     ty: tool name string ("fountainPen", "Pencil",
              "Highlighter", ...), pe: pressure-enabled bool,
              c: ARGB uint32 color, s: base size, sm: smoothing,
              i: page index, p: point array — binary structs of
              2x f32 (x, y) when pe=0, 3x f32 (x, y, pressure 0-1)
              when pe=1.

Saber's own pressure->width curve is not reimplemented (its source is
GPL-3.0); strokes carry the raw PRESSURE channel plus a constant-width
appearance of the base size, so `--fidelity raw`/`native` are faithful
and `exact` is approximate [inferred]. Saber is itself open source —
long-term the right move is contributing IR export upstream.

The tiny BSON walker below implements the subset Saber emits (from the
public BSON spec, bsonspec.org).
"""
from __future__ import annotations

import logging
import struct
import zipfile
from pathlib import Path

from ... import ir

_logger = logging.getLogger(__name__)

FORMAT_ID = "saber"

TOOL_FAMILY = {
    "fountainpen": ir.ToolFamily.PEN,
    "ballpoint": ir.ToolFamily.BALLPOINT,
    "ballpointpen": ir.ToolFamily.BALLPOINT,  # Saber >=1.35 tool name
    "fineliner": ir.ToolFamily.FINELINER,
    "brush": ir.ToolFamily.BRUSH,
    "pencil": ir.ToolFamily.PENCIL,
    "highlighter": ir.ToolFamily.HIGHLIGHTER,
    "shapepen": ir.ToolFamily.PEN,
}


class BsonError(ValueError):
    pass


def parse_bson(buf: bytes, pos: int = 0) -> tuple[dict, int]:
    """Minimal BSON document parser (subset Saber uses)."""
    total = struct.unpack_from("<i", buf, pos)[0]
    end = pos + total - 1  # trailing NUL
    pos += 4
    out: dict = {}
    while pos < end:
        t = buf[pos]
        pos += 1
        name_end = buf.index(0, pos)
        name = buf[pos:name_end].decode()
        pos = name_end + 1
        if t == 0x01:
            out[name] = struct.unpack_from("<d", buf, pos)[0]
            pos += 8
        elif t == 0x02:
            n = struct.unpack_from("<i", buf, pos)[0]
            pos += 4
            out[name] = buf[pos:pos + n - 1].decode()
            pos += n
        elif t in (0x03, 0x04):
            sub, pos = parse_bson(buf, pos)
            out[name] = list(sub.values()) if t == 0x04 else sub
        elif t == 0x05:
            n = struct.unpack_from("<i", buf, pos)[0]
            out[name] = buf[pos + 5:pos + 5 + n]
            pos += 5 + n
        elif t == 0x08:
            out[name] = bool(buf[pos])
            pos += 1
        elif t == 0x0A:
            out[name] = None
        elif t == 0x10:
            out[name] = struct.unpack_from("<i", buf, pos)[0]
            pos += 4
        elif t == 0x12:
            out[name] = struct.unpack_from("<q", buf, pos)[0]
            pos += 8
        else:
            raise BsonError(f"unsupported BSON type 0x{t:02x} for {name!r}")
    return out, end + 1


def _argb(value: int) -> tuple[ir.Color, float]:
    value &= 0xFFFFFFFF
    a = (value >> 24) / 255.0
    return ir.Color(((value >> 16) & 255) / 255.0,
                    ((value >> 8) & 255) / 255.0,
                    (value & 255) / 255.0), a


def _stroke(s: dict) -> ir.Stroke | None:
    points = s.get("p") or []
    if not points:
        return None
    pressure_enabled = bool(s.get("pe"))
    xs, ys, pressures = [], [], []
    for blob in points:
        if not isinstance(blob, bytes) or len(blob) < 8:
            return None
        x, y = struct.unpack_from("<2f", blob)
        xs.append(x)
        ys.append(y)
        if pressure_enabled and len(blob) >= 12:
            pressures.append(struct.unpack_from("<f", blob, 8)[0])
    tool_name = str(s.get("ty", ""))
    family = TOOL_FAMILY.get(tool_name.lower(), ir.ToolFamily.UNKNOWN)
    color, alpha = _argb(int(s.get("c", 0xFF000000)))
    size = float(s.get("s", 2.0))
    is_highlight = family is ir.ToolFamily.HIGHLIGHTER

    channels = {ir.Channel.WIDTH: [size] * len(xs)}
    if pressures and len(pressures) == len(xs):
        channels[ir.Channel.PRESSURE] = pressures
    return ir.Stroke(
        x=xs, y=ys,
        tool=ir.ToolRef(
            family=family,
            native=ir.NativeTool(FORMAT_ID, tool_name, {
                "size": size, "smoothing": s.get("sm"),
                "pressure_enabled": pressure_enabled,
                # every scalar stroke key verbatim (insertion-ordered) so
                # the writer can round-trip tool options it doesn't model
                # (e.g. Pencil's sl/ts/te) — the app's loader may require
                # them
                "raw": {k: v for k, v in s.items()
                        if k not in ("p", "i")},
            }),
        ),
        color=color,
        channels=channels,
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT,
            width=size,
            color=color,
            opacity=alpha,
            cap=ir.LineCap.ROUND,
            underlay=is_highlight,
            blend=ir.BlendMode.DARKEN if is_highlight else ir.BlendMode.NORMAL,
        ),
    )


def read_sbn2(data: bytes, title: str = "") -> ir.Document:
    doc, _ = parse_bson(data)
    pages = []
    for page in doc.get("z") or []:
        if not isinstance(page, dict):
            continue
        strokes = [st for st in (
            _stroke(s) for s in page.get("s") or [] if isinstance(s, dict)
        ) if st is not None]
        texts = []
        for q in page.get("q") or []:
            if isinstance(q, dict) and isinstance(q.get("insert"), str):
                texts.append(ir.TextBlock(x=0.0, y=0.0, text=q["insert"]))
        pages.append(ir.Page(
            bounds=ir.Rect(0.0, 0.0,
                           float(page.get("w", 1000.0)),
                           float(page.get("h", 1400.0))),
            # Saber canvas units; ~A5-ish page. 1000u -> 595pt [inferred].
            point_scale=595.0 / 1000.0,
            layers=[ir.Layer(strokes=strokes, texts=texts)],
        ))
    return ir.Document(
        format_id=FORMAT_ID,
        title=title,
        pages=pages,
        metadata={"sbn_version": doc.get("v")},
    )


class SaberReader:
    format_id = FORMAT_ID
    extensions = (".sba", ".sbn2", ".sbn")

    def detect(self, path: Path) -> bool:
        try:
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as zf:
                    return any(n.endswith(".sbn2") or n.endswith(".sbn")
                               for n in zf.namelist())
            with open(path, "rb") as f:
                head = f.read(16)
            total = struct.unpack_from("<i", head, 0)[0]
            return (path.stat().st_size == total
                    and head[4] == 0x10 and head[5:7] == b"v\x00")
        except (OSError, struct.error, IndexError):
            return False

    def read(self, path: Path) -> ir.Document:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zf:
                name = next(n for n in zf.namelist()
                            if n.endswith((".sbn2", ".sbn")))
                return read_sbn2(zf.read(name), title=path.stem)
        return read_sbn2(path.read_bytes(), title=path.stem)
