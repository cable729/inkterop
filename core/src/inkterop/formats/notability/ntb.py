"""Notability modern .ntb export (zip + FlatBuffers noteBundle) -> IR.

An .ntb (Mac app 16.x "Save as…" export) is a zip: `version` (ASCII "1"),
`manifest.json` ({"appVersion": ...}), `thumbnail.png`, and `noteBundle` —
a schema-less-decoded FlatBuffers blob holding an op log (the same table
encoding as the app container's local-persistence notes/<UUID> blobs).

Field indices below were established empirically against a self-generated
sample and validated by rendering against the app's own thumbnail; see
docs/formats/notability.md (".ntb" section) for the byte-level spec and
per-claim confidence. Summary of what this reader consumes:

  root table:      field_4 created (u64 ms), field_5 note UUID string,
                   field_6 vector<op table>
  op table:        field_4 op type (u8; 1 = document metadata, 15 = stroke),
                   field_5 payload table
  metadata payload: field_0.field_0 title string,
                   field_1.field_0.field_3 page size (2 x f32, pt)
  stroke payload:  field_1 origin (2 x f32, page pt, = first anchor point),
                   field_4 tool (u8: 0 pen, 1 pencil, 2 highlighter),
                   field_7 RGBA (4 bytes), field_8 base width f32 (pt),
                   field_9 point blob (ubyte vector)

  point blob:      u8 coord_fmt (0 = f16, 1 = f32; +4 zero bytes when 1),
                   u16 point count P, u8 = 3, u32 = 0, then P-1 records
                   [width f16, f16 = 1.0, 0xff, u16 = 0] + cubic Bezier
                   segment (c1, c2, end) as (x, y) pairs relative to the
                   origin, then a 6-byte tail [width f16, f16, 0xff, 0x00]
                   for the final anchor. Widths are multipliers on the
                   base width (pressure profile).

Strokes are stored as fitted cubic Bezier chains; this reader flattens
each segment at SAMPLES_PER_SEGMENT parameter steps and interpolates the
per-anchor width multipliers across the samples.

Caveat: the op log has only been observed for freshly-drawn notes
(create + add-stroke ops). Erase/move/undo semantics are unmapped, so an
edited note may contain superseded strokes this reader still renders —
see "Open questions" in the format doc.
"""
from __future__ import annotations

import json
import struct
import zipfile
from pathlib import Path

from ... import ir

FORMAT_ID = "notability"

SAMPLES_PER_SEGMENT = 4
DEFAULT_PAGE = (612.0, 792.0)  # US Letter, matches observed metadata

OP_DOC_METADATA = 1
OP_STROKE = 15

TOOL_FAMILIES = {
    0: ir.ToolFamily.PEN,
    1: ir.ToolFamily.PENCIL,
    2: ir.ToolFamily.HIGHLIGHTER,
}


class _Table:
    """Minimal FlatBuffers table accessor (vtable-indexed fields)."""

    def __init__(self, buf: bytes, pos: int):
        self.buf = buf
        self.pos = pos
        vpos = pos - struct.unpack_from("<i", buf, pos)[0]
        vbytes = struct.unpack_from("<H", buf, vpos)[0]
        n = (vbytes - 4) // 2
        self._slots = struct.unpack_from(f"<{n}H", buf, vpos + 4)

    def _field(self, i: int) -> int | None:
        if i >= len(self._slots) or not self._slots[i]:
            return None
        return self.pos + self._slots[i]

    def u8(self, i: int) -> int | None:
        p = self._field(i)
        return None if p is None else self.buf[p]

    def u64(self, i: int) -> int | None:
        p = self._field(i)
        return None if p is None else struct.unpack_from("<Q", self.buf, p)[0]

    def f32s(self, i: int, n: int) -> tuple[float, ...] | None:
        p = self._field(i)
        return None if p is None else struct.unpack_from(f"<{n}f", self.buf, p)

    def bytes_at(self, i: int, n: int) -> bytes | None:
        p = self._field(i)
        return None if p is None else self.buf[p:p + n]

    def _indirect(self, i: int) -> int | None:
        p = self._field(i)
        return None if p is None else p + struct.unpack_from("<I", self.buf, p)[0]

    def table(self, i: int) -> "_Table | None":
        t = self._indirect(i)
        return None if t is None else _Table(self.buf, t)

    def string(self, i: int) -> str | None:
        t = self._indirect(i)
        if t is None:
            return None
        n = struct.unpack_from("<I", self.buf, t)[0]
        return self.buf[t + 4:t + 4 + n].decode("utf-8")

    def vector(self, i: int) -> list[int] | None:
        """Vector of table offsets -> absolute table positions."""
        t = self._indirect(i)
        if t is None:
            return None
        n = struct.unpack_from("<I", self.buf, t)[0]
        out = []
        for k in range(n):
            e = t + 4 + 4 * k
            out.append(e + struct.unpack_from("<I", self.buf, e)[0])
        return out

    def byte_vector(self, i: int) -> bytes | None:
        t = self._indirect(i)
        if t is None:
            return None
        n = struct.unpack_from("<I", self.buf, t)[0]
        return self.buf[t + 4:t + 4 + n]


def decode_point_blob(blob: bytes) -> tuple[list, list[float]]:
    """-> ([(c1, c2, end) segments, origin-relative], anchor width mults)."""
    coord_fmt, npts, three = struct.unpack_from("<BHB", blob, 0)
    if coord_fmt not in (0, 1):
        raise ValueError(f"unknown .ntb point coordinate format {coord_fmt}")
    pair_fmt, pair_size = ("<2f", 8) if coord_fmt else ("<2e", 4)
    pos = 12 if coord_fmt == 1 else 8  # fmt 1: 4 extra (zero) header bytes
    segments: list[tuple] = []
    widths: list[float] = []
    while len(blob) - pos > 6:
        widths.append(struct.unpack_from("<e", blob, pos)[0])
        pos += 7
        c1, c2, end = (struct.unpack_from(pair_fmt, blob, pos + pair_size * k)
                       for k in range(3))
        segments.append((c1, c2, end))
        pos += 3 * pair_size
    if len(blob) - pos != 6:
        raise ValueError(f".ntb point blob framing broke at {pos}/{len(blob)}")
    widths.append(struct.unpack_from("<e", blob, pos)[0])  # final anchor
    if len(segments) + 1 != npts:
        raise ValueError(f".ntb point blob: {len(segments)} segments "
                         f"for declared {npts} points")
    return segments, widths


def _flatten(segments: list, widths: list[float],
             samples: int = SAMPLES_PER_SEGMENT):
    """Sample the cubic chain (start = (0, 0)) -> xs, ys, width mults."""
    xs, ys, ws = [0.0], [0.0], [widths[0]]
    x0 = y0 = 0.0
    for k, (c1, c2, end) in enumerate(segments):
        w0, w1 = widths[k], widths[k + 1]
        for j in range(1, samples + 1):
            t = j / samples
            u = 1.0 - t
            b0, b1, b2, b3 = u * u * u, 3 * u * u * t, 3 * u * t * t, t ** 3
            xs.append(b0 * x0 + b1 * c1[0] + b2 * c2[0] + b3 * end[0])
            ys.append(b0 * y0 + b1 * c1[1] + b2 * c2[1] + b3 * end[1])
            ws.append(w0 + (w1 - w0) * t)
        x0, y0 = end
    return xs, ys, ws


def _stroke_from_payload(p: _Table) -> ir.Stroke:
    ox, oy = p.f32s(1, 2)
    tool_id = p.u8(4) or 0
    r, g, b, a = p.bytes_at(7, 4)
    base_width = p.f32s(8, 1)[0]
    segments, mults = decode_point_blob(p.byte_vector(9))
    rel_x, rel_y, ws = _flatten(segments, mults)

    alpha = a / 255.0
    color = ir.Color(r / 255.0, g / 255.0, b / 255.0)
    family = TOOL_FAMILIES.get(tool_id, ir.ToolFamily.UNKNOWN)
    return ir.Stroke(
        x=[ox + v for v in rel_x],
        y=[oy + v for v in rel_y],
        tool=ir.ToolRef(
            family=family,
            native=ir.NativeTool(FORMAT_ID, tool_id,
                                 {"width": base_width, "alpha": alpha}),
        ),
        color=color,
        channels={ir.Channel.WIDTH: [base_width * w for w in ws]},
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_VARIABLE,
            color=color,
            opacity=alpha,
            underlay=(family is ir.ToolFamily.HIGHLIGHTER),
        ),
    )


def read_note_bundle(buf: bytes) -> ir.Document:
    root = _Table(buf, struct.unpack_from("<I", buf, 0)[0])
    ops = root.vector(6)
    if ops is None:
        raise ValueError("noteBundle root has no op vector "
                         "(unrecognized .ntb layout)")

    title = ""
    page_w, page_h = DEFAULT_PAGE
    strokes: list[ir.Stroke] = []
    for op_pos in ops:
        op = _Table(buf, op_pos)
        op_type = op.u8(4)
        payload = op.table(5)
        if payload is None:
            continue
        if op_type == OP_DOC_METADATA:
            title_tbl = payload.table(0)
            if title_tbl is not None:
                title = title_tbl.string(0) or title
            page_tbl = payload.table(1)
            page_attrs = page_tbl.table(0) if page_tbl else None
            size = page_attrs.f32s(3, 2) if page_attrs else None
            if size:
                page_w, page_h = size
        elif op_type == OP_STROKE:
            strokes.append(_stroke_from_payload(payload))

    # Continuous vertical scroll: one page, grown to the ink extents.
    max_y = max((y for s in strokes for y in s.y), default=0.0)
    page = ir.Page(
        bounds=ir.Rect(0.0, 0.0, page_w, max(page_h, max_y)),
        point_scale=1.0,
        layers=[ir.Layer(strokes=strokes)],
    )
    doc = ir.Document(format_id=FORMAT_ID, title=title, pages=[page])
    created = root.u64(4)
    if created:
        doc.metadata["created_unix_ms"] = created
    uuid = root.string(5)
    if uuid:
        doc.metadata["notability_uuid"] = uuid
    return doc


class NtbReader:
    format_id = FORMAT_ID
    extensions = (".ntb",)

    def detect(self, path: Path) -> bool:
        if not zipfile.is_zipfile(path):
            return False
        try:
            with zipfile.ZipFile(path) as zf:
                return "noteBundle" in zf.namelist()
        except (OSError, zipfile.BadZipFile):
            return False

    def read(self, path: Path) -> ir.Document:
        with zipfile.ZipFile(path) as zf:
            doc = read_note_bundle(zf.read("noteBundle"))
            try:
                manifest = json.loads(zf.read("manifest.json"))
                doc.metadata["app_version"] = manifest.get("appVersion")
            except (KeyError, ValueError):
                pass
        doc.title = doc.title or path.stem
        return doc
