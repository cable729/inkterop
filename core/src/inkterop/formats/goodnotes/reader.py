"""GoodNotes 6 (.goodnotes) -> IR. Ink strokes + color; experimental.

Container (verified against public samples, 2026-07): ZIP with
`notes/<UUID>` per page (length-delimited protobuf records; a record whose
field #7 is present holds one stroke), `index.notes.pb` (document/page
index), `attachments/<UUID>` (PDF backgrounds), `schema.pb` (2-byte
version marker, NOT a schema).

Stroke message (field numbers within record field #7):
  #1  UUID string of the stroke
  #2  geometry: Apple-framed LZ4 -> `tpl\\0` section blob (see below)
  #3  pen style: absent/0 = constant-width ball pen, 1 = pressure pen
      (fountain/brush/marker), 5 = pencil
  #4  color: float32 subfields 1=R 2=G 3=B 4=A, omitted = 0.0
  #5  varint 1 = highlighter
  #7  {index, nonce} identity msg — NOT a pen type (2026-07-10 finding)
  #20 {1: ""} = marker; empty bytes on every other stroke
Geometry blob: `tpl\\0` + u32 total length + ASCII type-descriptor string +
typed sections (see parse_tpl). Coordinates are PDF points @72dpi,
top-left origin, width has pressure baked in (device-rendered width, like
reMarkable). Fountain vs brush is NOT stored per stroke — both read as
generic PEN.

Page list, order and dimensions replay from `index.events.pb` (paper
definitions + page-created events; the app derives the document from this
journal, and a page whose `notes/` blob is empty or absent still exists).
Falls back to `index.notes.pb`/zip order + A4 when the events log is
missing or its page model doesn't match the container.

Not yet decoded: erasers (field-14 re-records are NOT tombstones —
docs/erase-audit.md), images, text boxes, PDF-background linkage.
Tracked in docs/formats/goodnotes.md with confidence markers.
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

    Pressure pens store either flat (x, y, w) triplets or 9-float sample
    pairs (x1, y1, w1, x2, y2, w2, alt1, alt2, k) — alt* are per-sample
    Apple Pencil altitude-like angles (0.0 on Mac/mouse), k [unknown].
    Which layout is in use is flagged by bit 2 of the first u16 section
    (values {4, 5} = 9-float, {0, 1} = triplets) [verified across schema
    24+25 corpora, 2026-07-10]. Constant pens/highlighters store one
    width scalar + arrays of (x, y) structs; pencils 5+-float structs.
    Shape objects have empty geometry here (stored elsewhere, [unknown]).
    """
    try:
        sections = parse_tpl(blob)
    except (WireError, ValueError):
        return [tuple(t) for t in _scan_fallback(blob)], False

    scalar_width = next(
        (v[0] for k, s, v in sections if k == "scalar" and s == "u"), None
    )
    flag_bits = next((set(v) for k, s, v in sections
                      if k == "array" and s == "v"), set())
    has_tilt = any(f & 4 for f in flag_bits)

    def _pt_ok(t):
        return _plausible(*t) or _is_break(*t)

    for kind, spec, vals in sections:
        if kind != "array" or spec != "u" or len(vals) < 9:
            continue
        # 9-float sample pairs; without the tilt flag the trailing
        # (alt1, alt2) are zeros and the layout is detectable directly.
        if len(vals) % 9 == 0:
            groups = [vals[i:i + 9] for i in range(0, len(vals), 9)]
            if ((has_tilt or all(_is_break(*g[6:9]) for g in groups))
                    and all(_pt_ok(g[0:3]) and _pt_ok(g[3:6])
                            for g in groups)):
                path: list[tuple[float, float, float]] = []
                for g in groups:
                    for t in (tuple(g[0:3]), tuple(g[3:6])):
                        if not path or (abs(path[-1][0] - t[0]) > 1e-6
                                        or abs(path[-1][1] - t[1]) > 1e-6):
                            path.append(t)
                if sum(1 for t in path if not _is_break(*t)) >= 2:
                    return path, False
        # Flat (x, y, w) triplets. Never valid when the tilt flag is set
        # (a 9-float array is divisible by 3 too and would misparse into
        # phantom points near the origin).
        if not has_tilt and len(vals) % 3 == 0:
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
    # Dots are a single segment struct (2 points), so accept length-1
    # arrays; ties between them prefer the widest struct (the 11-float
    # pencil segment over its 5-float anchor).
    struct_arrays = [(spec, vals) for kind, spec, vals in sections
                     if kind == "struct_array" and vals]
    for spec, vals in sorted(struct_arrays,
                             key=lambda sv: (-len(sv[1]), -len(sv[0]))):
        n = len(spec)
        for (i, j), (k, l) in ((( 0, 1), (2, 3)), ((1, 2), (6, 7))):
            if max(k, l) >= n:
                continue
            pts = [(t[i], t[j]) for t in vals] + [(vals[-1][k], vals[-1][l])]
            if all(0 <= x <= _MAX_X and 0 <= y <= _MAX_Y for x, y in pts):
                return [(x, y, w) for x, y in pts], True
        if n == 2 and len(vals) >= 2:  # plain (x, y) pairs
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


# Pen style (2026-07-10, iPad calibration page + Mac mixed-pens fixture —
# docs/formats/goodnotes.md "Pen style"). Encoded across three stroke
# fields: #3 varint (absent/0 = constant-width ball pen, 1 = pressure pen,
# 5 = pencil), #5 varint 1 = highlighter, #20 = {1: ""} submessage = marker
# (every other stroke carries field 20 as EMPTY bytes). Fountain vs brush
# pen is NOT distinguishable in the stroke record (all fields identical) —
# both map to the generic PEN family. Stroke field 7 turned out to be an
# {index, nonce} identity message; the old field-7 "pen-type id" table was
# a draw-order coincidence.
_STYLE_FAMILY = {
    "ball": ir.ToolFamily.BALLPOINT,
    "pressure": ir.ToolFamily.PEN,  # fountain or brush
    "pencil": ir.ToolFamily.PENCIL,
    "marker": ir.ToolFamily.MARKER,
    "highlighter": ir.ToolFamily.HIGHLIGHTER,
}


def _varint(fields: dict, num: int) -> int:
    if num in fields and isinstance(fields[num][0].value, int):
        return fields[num][0].value
    return 0


def _pen_style(fields: dict) -> str:
    """Stroke message fields -> symbolic pen style (keys of _STYLE_FAMILY)."""
    if _varint(fields, 5) == 1:
        return "highlighter"
    f3 = _varint(fields, 3)
    if f3 == 5:
        return "pencil"
    if f3 == 1:
        f20 = fields[20][0].value if 20 in fields else b""
        if isinstance(f20, bytes) and f20:
            try:
                if 1 in fields_by_number(f20):
                    return "marker"
            except WireError:
                pass
        return "pressure"
    return "ball"


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

    style = _pen_style(fields)
    family = _STYLE_FAMILY[style]
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
                native=ir.NativeTool(FORMAT_ID, style, {
                    "field3": _varint(fields, 3),
                    "field5": _varint(fields, 5),
                }),
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
    if len(strokes) == 1:
        # Raw stroke message, replayed by the writer with only the
        # journal uuid/echo fields rewritten — the app's geometry blob
        # carries per-point sections our minimal encoder can't rebuild
        # (a re-encoded brush stroke renders as a blob in-app).
        strokes[0].extra.setdefault(FORMAT_ID, {})["record"] = stroke_msg.hex()
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


def _ascii_field(fields: dict, number: int) -> str | None:
    for f in fields.get(number, []):
        if isinstance(f.value, bytes):
            try:
                return f.value.decode("ascii")
            except UnicodeDecodeError:
                return None
    return None


def _content_uuid(entity: str) -> str | None:
    """Page ENTITY uuid -> page CONTENT uuid (the notes/<uuid> member).

    The app allocates them adjacently: content = entity + 1 in the last
    hex group. [verified] on two independent exports (mixed-pens fixture,
    calibration notebook — two pages each pair adjacent) + app import
    behavior (random entity uuids leave the page blank).
    """
    head, _, tail = entity.rpartition("-")
    try:
        n = int(tail, 16) + 1
    except ValueError:
        return None
    if not head or n >= 1 << (4 * len(tail)):
        return None
    return f"{head}-{n:0{len(tail)}X}"


def _events_page_model(
    zf: zipfile.ZipFile,
) -> list[tuple[str, tuple[float, float] | None]] | None:
    """Replay index.events.pb into the page list the app derives from it:
    [(content_uuid, paper (w, h) in points | None)] in display order.

    The events journal is the document's source of truth — a page whose
    notes blob is empty (or absent) still exists as a page-created event.
    Replayed records (docs/formats/goodnotes.md): paper definitions
    (top field 2: body 2 = paper uuid, body 8 = {1: w, 2: h} float32) and
    page-created events (top field 54: body 2 = page entity uuid,
    body 3.1 = paper ref, body 4.1 = lexicographic order key). Returns
    None when nothing replayable is present.
    """
    try:
        records = split_delimited(zf.read("index.events.pb"))
    except (KeyError, WireError):
        return None
    papers: dict[str, tuple[float, float]] = {}
    created: list[tuple[str, str, str | None]] = []  # (order, entity, paper)
    for rec in records:
        try:
            fields = fields_by_number(rec)
            if 2 in fields and isinstance(fields[2][0].value, bytes):
                body = fields_by_number(fields[2][0].value)
                paper_uuid = _ascii_field(body, 2)
                if paper_uuid and 8 in body:
                    dims = fields_by_number(body[8][0].value)
                    if 1 in dims and 2 in dims:
                        w, h = dims[1][0].value, dims[2][0].value
                        if (isinstance(w, float) and isinstance(h, float)
                                and 0 < w <= _MAX_X and 0 < h <= _MAX_Y):
                            papers[paper_uuid] = (w, h)
            elif 54 in fields and isinstance(fields[54][0].value, bytes):
                body = fields_by_number(fields[54][0].value)
                entity = _ascii_field(body, 2)
                order = paper = None
                if 4 in body and isinstance(body[4][0].value, bytes):
                    order = _ascii_field(fields_by_number(body[4][0].value), 1)
                if 3 in body and isinstance(body[3][0].value, bytes):
                    paper = _ascii_field(fields_by_number(body[3][0].value), 1)
                if entity:
                    created.append((order or "", entity, paper))
        except (WireError, TypeError, IndexError):
            continue
    if not created:
        return None
    model = []
    for _, entity, paper in sorted(created, key=lambda c: c[0]):
        content = _content_uuid(entity)
        if content is not None:
            model.append((content, papers.get(paper)))
    return model or None


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
            members = {n.removeprefix("notes/") for n in zf.namelist()
                       if n.startswith("notes/") and not n.endswith("/")}
            # Event replay first: the journal is the app's source of
            # truth for which pages exist, their order and paper size.
            # Sanity-check it against the container (if no replayed page
            # matches a notes/ member, the entity->content adjacency
            # doesn't hold for this file) and keep index.notes.pb order
            # as the fallback, appending any members replay missed.
            model = _events_page_model(zf)
            if model and any(uid in members for uid, _ in model):
                claimed = {uid for uid, _ in model}
                page_list = model + [(uid, None) for uid in _page_order(zf)
                                     if uid not in claimed]
            else:
                page_list = [(uid, None) for uid in _page_order(zf)]
            pages = []
            for page_uuid, paper_size in page_list:
                raw = (zf.read(f"notes/{page_uuid}")
                       if page_uuid in members else b"")
                strokes = []
                meta_record_hex = None
                meta_payload_hex = None
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
                            # leading per-page record — keep the raw bytes
                            # so the writer can replay them verbatim. In
                            # schema-25 journals this is an event HEADER
                            # whose payload is the NEXT record; capture
                            # that too (writer must replay the pair —
                            # unpaired records fail app import).
                            meta_record_hex = rec.hex()
                        elif (i == 1 and not found
                              and meta_record_hex is not None):
                            try:
                                nums = set(fields_by_number(rec))
                            except WireError:
                                nums = set()
                            if nums == {9}:
                                meta_payload_hex = rec.hex()
                        strokes.extend(found)
                # Paper size replays from the events journal; without one
                # assume A4. Either way grow to the ink extents so nothing
                # clips on larger papers.
                page_w, page_h = paper_size or A4_PT
                xs = [x for s in strokes for x in s.x]
                ys = [y for s in strokes for y in s.y]
                margin = 12.0
                bounds = ir.Rect(
                    min([0.0] + ([min(xs) - margin] if xs else [])),
                    min([0.0] + ([min(ys) - margin] if ys else [])),
                    max([page_w] + ([max(xs) + margin] if xs else [])),
                    max([page_h] + ([max(ys) + margin] if ys else [])),
                )
                pages.append(ir.Page(
                    bounds=bounds,
                    point_scale=1.0,  # coordinates are already PDF points
                    layers=[ir.Layer(strokes=strokes)],
                    extra={"goodnotes": {
                        "page_uuid": page_uuid,
                        **({"meta_record": meta_record_hex}
                           if meta_record_hex else {}),
                        **({"meta_payload": meta_payload_hex}
                           if meta_payload_hex else {}),
                    }},
                ))
            schema_version = None
            try:
                raw_schema = zf.read("schema.pb")
                if len(raw_schema) >= 2 and raw_schema[0] == 0x08:
                    v, shift, pos = 0, 0, 1
                    while pos < len(raw_schema):
                        byte = raw_schema[pos]
                        v |= (byte & 0x7F) << shift
                        pos += 1
                        if not byte & 0x80:
                            break
                        shift += 7
                    schema_version = v
            except KeyError:
                pass
        return ir.Document(
            format_id=FORMAT_ID,
            title=path.stem,
            pages=pages,
            extra=({"goodnotes": {"schema_version": schema_version}}
                   if schema_version is not None else {}),
        )
