"""GoodNotes 6 (.goodnotes) -> IR. Ink strokes + color; experimental.

Container (verified against public samples, 2026-07): ZIP with
`notes/<UUID>` per page (length-delimited protobuf records; a record whose
field #7 is present holds one stroke), `index.notes.pb` (document/page
index), `attachments/<UUID>` (PDF backgrounds), `schema.pb` (2-byte
version marker, NOT a schema).

Stroke message (field numbers within record field #7):
  #1  UUID string of the stroke
  #2  geometry: Apple-framed LZ4 -> `tpl\\0` section blob (see below)
  #4  color: float32 subfields 1=R 2=G 3=B 4=A, omitted = 0.0
Geometry blob: `tpl\\0` + u32 total length + ASCII type-descriptor string +
u16 metadata arrays + sections of [u32 float_count][float32 * count]. The
rendered path is the FIRST section at offset >= 64 holding >= 2 plausible
(x, y, width) triplets; coordinates are PDF points @72dpi, top-left origin,
width has pressure baked in (device-rendered width, like reMarkable).

Not yet decoded: pen type/highlighter flags, erasers, images, text boxes,
page dimensions (A4 assumed), PDF-background linkage. Tracked in
docs/formats/goodnotes.md with confidence markers.
"""
from __future__ import annotations

import logging
import struct
import zipfile
from pathlib import Path

from ... import ir
from .wire import (
    WireError,
    apple_lz4_decompress,
    fields_by_number,
    split_delimited,
)

_logger = logging.getLogger(__name__)

FORMAT_ID = "goodnotes"

A4_PT = (595.28, 841.89)  # [inferred] pages observed are A4; dims field unknown

# Plausibility window for (x, y, width) triplets, in points.
_MAX_X, _MAX_Y, _MAX_W = 2000.0, 2000.0, 60.0


def _plausible(x: float, y: float, w: float) -> bool:
    return (0.0 <= x <= _MAX_X and 0.0 <= y <= _MAX_Y
            and 0.01 < w <= _MAX_W)


def _is_break(x: float, y: float, w: float) -> bool:
    """(~0, ~0, *) triplets separate sub-paths within one stroke record."""
    return abs(x) < 0.01 and abs(y) < 0.01


def split_subpaths(
    path: list[tuple[float, float, float]],
) -> list[list[tuple[float, float, float]]]:
    out: list[list[tuple[float, float, float]]] = [[]]
    for t in path:
        if _is_break(*t):
            if out[-1]:
                out.append([])
        else:
            out[-1].append(t)
    return [p for p in out if p]


_SCALAR_FMT = {"v": ("<H", 2), "u": ("<f", 4), "f": ("<f", 4)}  # 'f' [unknown]


def _parse_sig(sig: str) -> list[tuple[str, str]]:
    """Type signature -> [(kind, spec)]: ("scalar", "v"), ("array", "u"),
    ("struct_array", "uu...")."""
    tokens: list[tuple[str, str]] = []
    i = 0
    while i < len(sig):
        if sig.startswith("A(S(", i):
            j = sig.index(")", i + 4)
            tokens.append(("struct_array", sig[i + 4:j]))
            i = j + 2  # skip "))"
        elif sig.startswith("A(", i):
            tokens.append(("array", sig[i + 2]))
            i = sig.index(")", i) + 1
        elif sig[i] in _SCALAR_FMT:
            tokens.append(("scalar", sig[i]))
            i += 1
        else:
            raise WireError(f"unknown signature token at {i} in {sig!r}")
    return tokens


def parse_tpl(blob: bytes) -> list[tuple[str, str, list]]:
    """Decompressed geometry blob -> typed sections [(kind, spec, values)].

    Layout (our finding; goes beyond goodparse's documented scan heuristic):
      "tpl\\0" + u32 total_length
      + ASCII type signature, NUL-terminated
      + sections in signature order.
    Signature grammar: scalars `v` (u16) and `u` (float32) read one value;
    `A(x)` reads u32 count + count scalars; `A(S(xx...))` reads u32 count +
    count structs (values flattened per struct into tuples). `f` observed
    only with count 0; assumed 4 bytes [unknown]. Parsing must consume the
    blob exactly — any residue raises. [verified] on 2024-era public
    samples (schema 24) and fresh Mac exports (schema 25).
    """
    if blob[:4] != b"tpl\x00":
        raise WireError(f"geometry blob lacks tpl header: {blob[:4]!r}")
    total = struct.unpack_from("<I", blob, 4)[0]
    if total != len(blob):
        raise WireError(f"tpl length {total} != blob {len(blob)}")
    end = blob.index(0, 8)
    sig = blob[8:end].decode("ascii", "replace")
    pos = end + 1

    sections: list[tuple[str, str, list]] = []
    try:
        for kind, spec in _parse_sig(sig):
            if kind == "scalar":
                fmt, size = _SCALAR_FMT[spec]
                sections.append((kind, spec,
                                 [struct.unpack_from(fmt, blob, pos)[0]]))
                pos += size
            elif kind == "array":
                count = struct.unpack_from("<I", blob, pos)[0]
                pos += 4
                fmt, size = _SCALAR_FMT[spec]
                vals = list(struct.unpack_from(f"<{count}{fmt[1]}", blob, pos))
                pos += size * count
                sections.append((kind, spec, vals))
            else:  # struct_array
                count = struct.unpack_from("<I", blob, pos)[0]
                pos += 4
                width = len(spec)  # all observed struct fields are 'u'
                flat = struct.unpack_from(f"<{count * width}f", blob, pos)
                pos += 4 * count * width
                vals = [tuple(flat[k * width:(k + 1) * width])
                        for k in range(count)]
                sections.append((kind, spec, vals))
    except struct.error as e:
        raise WireError(f"tpl section overruns blob ({sig=}): {e}") from e
    if pos != len(blob):
        raise WireError(f"tpl sections ended at {pos}, blob is {len(blob)}B "
                        f"({sig=})")
    return sections


def extract_path(blob: bytes) -> tuple[list[tuple[float, float, float]], bool]:
    """Geometry blob -> ((x, y, width) triplets, constant_width flag).

    Pressure pens store flat float triplets (per-point width); constant
    pens/highlighters store one width scalar + arrays of (x, y) structs;
    pencils store 5+-float structs whose first three are (x, y, w).
    Shape objects have empty geometry here (stored elsewhere, [unknown]).
    """
    try:
        sections = parse_tpl(blob)
    except (WireError, ValueError):
        return [tuple(t) for t in _scan_fallback(blob)], False

    scalar_width = next(
        (v[0] for k, s, v in sections if k == "scalar" and s == "u"), None
    )
    for kind, spec, vals in sections:
        if kind != "array" or spec != "u" or len(vals) < 9:
            continue
        # Brush pens: 9-float flattened segments
        # (x1, y1, w1, x2, y2, w2, 0, 0, k) — interleave endpoints.
        if len(vals) % 9 == 0:
            groups = [vals[i:i + 9] for i in range(0, len(vals), 9)]
            if all(_is_break(*g[6:9]) and _plausible(*g[0:3])
                   and _plausible(*g[3:6]) for g in groups):
                path: list[tuple[float, float, float]] = []
                for g in groups:
                    for t in (tuple(g[0:3]), tuple(g[3:6])):
                        if not path or (abs(path[-1][0] - t[0]) > 1e-6
                                        or abs(path[-1][1] - t[1]) > 1e-6):
                            path.append(t)
                if len(path) >= 2:
                    return path, False
        # Pressure pens: flat (x, y, w) triplets.
        if len(vals) % 3 == 0:
            triplets = [tuple(vals[i:i + 3])
                        for i in range(0, len(vals), 3)]
            if all(_plausible(*t) for t in triplets):
                return triplets, False

    # Segment structs: constant-width pens store chained line segments.
    # S(uuuu) = (x1, y1, x2, y2); pencil S(u*11) = (?, x1, y1, alt?, azi?, ?,
    # x2, y2, alt?, azi?, ?) — cols 3/4 sit at pi/6 & pi/3, Apple Pencil's
    # default altitude/azimuth [inferred]. Path = first endpoint of each
    # segment + the last segment's second endpoint.
    w = (scalar_width if scalar_width and 0 < scalar_width <= _MAX_W
         else 1.0)
    struct_arrays = [(spec, vals) for kind, spec, vals in sections
                     if kind == "struct_array" and len(vals) >= 2]
    for spec, vals in sorted(struct_arrays, key=lambda sv: -len(sv[1])):
        n = len(spec)
        for (i, j), (k, l) in ((( 0, 1), (2, 3)), ((1, 2), (6, 7))):
            if max(k, l) >= n:
                continue
            pts = [(t[i], t[j]) for t in vals] + [(vals[-1][k], vals[-1][l])]
            if all(0 <= x <= _MAX_X and 0 <= y <= _MAX_Y for x, y in pts):
                return [(x, y, w) for x, y in pts], True
        if n == 2:  # plain (x, y) pairs
            if all(0 <= x <= _MAX_X and 0 <= y <= _MAX_Y for x, y in vals):
                return [(x, y, w) for x, y in vals], True
    return [], scalar_width is not None


def _scan_fallback(blob: bytes) -> list[tuple[float, float, float]]:
    off = 8
    while off + 24 <= len(blob):
        run = []
        pos = off
        while pos + 12 <= len(blob):
            x, y, w = struct.unpack_from("<3f", blob, pos)
            if not _plausible(x, y, w):
                break
            run.append((x, y, w))
            pos += 12
        if len(run) >= 2:
            return run
        off += 4
    return []


# Pen type (stroke field 7 -> submessage field 1). Only the highlighter is
# behaviorally confirmed (24pt constant width, drawn as highlighter);
# remaining names await the labeled corpus (docs/corpus-protocol.md case 05).
_PEN_TYPE_FAMILY = {
    4: ir.ToolFamily.HIGHLIGHTER,  # [verified by width/appearance]
}
_SHAPE_PEN_TYPE = 7  # empty inline geometry; shape objects [inferred]


def _pen_type(fields: dict) -> int:
    if 7 not in fields:
        return 0
    try:
        sub = fields_by_number(fields[7][0].value)
        inner = sub.get(1)
        if inner and isinstance(inner[0].value, bytes):
            first = fields_by_number(inner[0].value).get(1)
            return int(first[0].value) if first else 0
    except (WireError, TypeError):
        pass
    return 0


def _strokes_from_record(record: bytes) -> list[ir.Stroke]:
    top = fields_by_number(record)
    if 7 not in top:
        return []
    stroke_msg = top[7][0].value
    fields = fields_by_number(stroke_msg)

    if 2 not in fields:
        return []
    try:
        blob = apple_lz4_decompress(fields[2][0].value)
        path, constant = extract_path(blob)
    except (WireError, struct.error) as e:
        _logger.warning("goodnotes stroke geometry undecodable: %s", e)
        return []
    if not path:
        return []

    r = g = b = a = 0.0
    if 4 in fields:
        for cf in fields_by_number(fields[4][0].value).items():
            num, fl = cf[0], cf[1][0].value
            if num == 1:
                r = fl
            elif num == 2:
                g = fl
            elif num == 3:
                b = fl
            elif num == 4:
                a = fl
    color = ir.Color(r, g, b, a if a > 0 else 1.0)

    pen_type = _pen_type(fields)
    family = _PEN_TYPE_FAMILY.get(pen_type, ir.ToolFamily.PEN)
    is_highlight = family is ir.ToolFamily.HIGHLIGHTER
    rgb = ir.Color(color.r, color.g, color.b)

    strokes = []
    for sub in split_subpaths(path):
        widths = [p[2] for p in sub]
        strokes.append(ir.Stroke(
            x=[p[0] for p in sub],
            y=[p[1] for p in sub],
            tool=ir.ToolRef(
                family=family,
                native=ir.NativeTool(FORMAT_ID, pen_type, {}),
            ),
            color=rgb,
            channels={ir.Channel.WIDTH: widths},
            appearance=ir.StrokeAppearance(
                mode=(ir.GeometryMode.STROKED_CONSTANT if constant
                      else ir.GeometryMode.STROKED_VARIABLE),
                width=widths[0] if constant else None,
                color=rgb,
                opacity=0.5 if is_highlight and color.a == 1.0 else color.a,
                blend=(ir.BlendMode.DARKEN if is_highlight
                       else ir.BlendMode.NORMAL),
                cap=ir.LineCap.SQUARE if is_highlight else ir.LineCap.ROUND,
                underlay=is_highlight,
            ),
        ))
    return strokes


def _page_order(zf: zipfile.ZipFile) -> list[str]:
    """Page UUID order from index.notes.pb when decodable, else zip order.

    [inferred] index.notes.pb record layout: each delimited record's field
    #1 is a page/document UUID string; observed to list pages in order.
    """
    names = [n.removeprefix("notes/") for n in zf.namelist()
             if n.startswith("notes/") and not n.endswith("/")]
    try:
        idx = zf.read("index.notes.pb")
        ordered = []
        for rec in split_delimited(idx):
            fields = fields_by_number(rec)
            for f in fields.get(1, []):
                if isinstance(f.value, bytes):
                    try:
                        uid = f.value.decode("ascii")
                    except UnicodeDecodeError:
                        continue
                    if uid in names:
                        ordered.append(uid)
        if ordered:
            return ordered + [n for n in names if n not in ordered]
    except (KeyError, WireError):
        pass
    return sorted(names)


class GoodnotesReader:
    format_id = FORMAT_ID
    extensions = (".goodnotes",)

    def detect(self, path: Path) -> bool:
        if not zipfile.is_zipfile(path):
            return False
        try:
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
            return any(n.startswith("notes/") for n in names) and (
                "index.notes.pb" in names or "schema.pb" in names
            )
        except (OSError, zipfile.BadZipFile):
            return False

    def read(self, path: Path) -> ir.Document:
        with zipfile.ZipFile(path) as zf:
            pages = []
            for page_uuid in _page_order(zf):
                raw = zf.read(f"notes/{page_uuid}")
                strokes = []
                meta_record_hex = None
                if raw:
                    try:
                        records = split_delimited(raw)
                    except WireError:
                        _logger.warning("goodnotes page %s: bad record stream",
                                        page_uuid)
                        records = []
                    for i, rec in enumerate(records):
                        try:
                            found = _strokes_from_record(rec)
                        except WireError:
                            continue
                        if i == 0 and not found:
                            # leading per-page metadata record — keep the
                            # raw bytes so the writer can replay them
                            # verbatim (fields largely [unknown])
                            meta_record_hex = rec.hex()
                        strokes.extend(found)
                # Page-dimension field is [unknown]; assume A4 but grow to
                # the ink extents so nothing clips on larger papers.
                xs = [x for s in strokes for x in s.x]
                ys = [y for s in strokes for y in s.y]
                margin = 12.0
                bounds = ir.Rect(
                    min([0.0] + ([min(xs) - margin] if xs else [])),
                    min([0.0] + ([min(ys) - margin] if ys else [])),
                    max([A4_PT[0]] + ([max(xs) + margin] if xs else [])),
                    max([A4_PT[1]] + ([max(ys) + margin] if ys else [])),
                )
                pages.append(ir.Page(
                    bounds=bounds,
                    point_scale=1.0,  # coordinates are already PDF points
                    layers=[ir.Layer(strokes=strokes)],
                    extra={"goodnotes": {
                        "page_uuid": page_uuid,
                        **({"meta_record": meta_record_hex}
                           if meta_record_hex else {}),
                    }},
                ))
        return ir.Document(
            format_id=FORMAT_ID,
            title=path.stem,
            pages=pages,
        )
