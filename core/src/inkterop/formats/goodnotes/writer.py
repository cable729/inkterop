"""IR -> GoodNotes 6 (.goodnotes). Experimental; exact inverse of reader.py.

Emits the FULL member set the Mac app writes (a minimal
schema/index/notes/thumbnail container was rejected by the first
app-import check, 2026-07-09): `document.info.pb` (empty — 0 bytes in
real exports), `index.search.pb` + `search/<uuid>` (minimal record, one
shared uuid with attachments as observed), `index.notes.pb` + one
`notes/<UUID>` record stream per page, `index.events.pb` (empty — the
real event journal's schema is [unknown]; a fresh import plausibly
regenerates it), `thumbnail.jpg`, `index.attachments.pb` +
`attachments/<uuid>` (a blank one-page PDF — GoodNotes stores paper
backgrounds as standalone PDF attachments; the notes blobs do not
reference them), and `schema.pb`.

The leading per-page metadata record's fields are largely [unknown]; the
reader captures the raw bytes into `page.extra["goodnotes"]["meta_record"]`
and the writer replays them verbatim on round-trips, synthesizing our
minimal skeleton only for foreign documents.

Every stroke is emitted with the pressure-pen tpl signature
(`vA(v)A(u)A(u)...`, flat (x, y, width) float32 triplets), regardless of
the source pen type — constant-width/pencil/brush section layouts are a
rendering optimization of the app, and the reader accepts triplets for
any pen type. Coordinates are PDF points, rebased to the page's top-left;
the page-dimension field is [unknown], so written pages implicitly assume
A4 (readers grow bounds to the ink extents).

Ships validated=False until the GoodNotes Mac app-import check passes
(docs/validated-writes.md).
"""
from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import Path
from typing import Any

from ... import ir
from ..base import Fidelity
from .._scale import unit_factor
from .reader import _MAX_W, _MAX_X, _MAX_Y, FORMAT_ID
from .wire import (
    apple_lz4_compress,
    encode_tpl,
    join_delimited,
    write_float32,
    write_len_delimited,
    write_varint_field,
)

# Emitted as schema.pb field 1 and per-record version fields. 24 is the
# widely-observed public-sample version; our reader accepts 24 and 25.
SCHEMA_VERSION = 24

# Pen-style wire encoding (docs/formats/goodnotes.md "Pen style"): stroke
# field 3 varint (1 = pressure pen, 5 = pencil, omitted = constant-width
# ball pen), field 5 = 1 for the highlighter, field 20 = {1: ""} for the
# marker (all other strokes carry field 20 as empty bytes). Symbolic style
# strings from a GoodNotes-sourced NativeTool round-trip verbatim.
_STYLE_FIELDS = {  # style -> (field3, field5, is_marker)
    "highlighter": (0, 1, False),
    "pencil": (5, 0, False),
    "ball": (0, 0, False),
    "marker": (1, 0, True),
    "pressure": (1, 0, False),
}
_FAMILY_STYLE = {
    ir.ToolFamily.HIGHLIGHTER: "highlighter",
    ir.ToolFamily.PENCIL: "pencil",
    ir.ToolFamily.MECHANICAL_PENCIL: "pencil",
    ir.ToolFamily.BALLPOINT: "ball",
    ir.ToolFamily.FINELINER: "ball",
    ir.ToolFamily.MARKER: "marker",
}

# Constant widths (points) for `native` fidelity, from the app's observed
# defaults: 24 pt highlighter, 18 pt marker, 1.56 pt ball pen/pencil.
_FAMILY_DEFAULT_WIDTH = {
    ir.ToolFamily.HIGHLIGHTER: 24.0,
    ir.ToolFamily.MARKER: 18.0,
    ir.ToolFamily.BRUSH: 3.0,
    ir.ToolFamily.CALLIGRAPHY: 3.0,
}
_DEFAULT_WIDTH = 1.56

_MIN_W = 0.02  # reader's plausibility window is 0.01 < w <= 60


def _clamp_point(x: float, y: float, w: float) -> tuple[float, float, float]:
    """Keep triplets inside the reader's plausibility window and away from
    the (~0, ~0) sub-path-break sentinel."""
    x = min(max(x, 0.0), _MAX_X)
    y = min(max(y, 0.0), _MAX_Y)
    w = min(max(w, _MIN_W), _MAX_W)
    if x < 0.01 and y < 0.01:
        x = 0.01
    return x, y, w


def _widths(s: ir.Stroke, k: float, fidelity: Fidelity) -> list[float]:
    """Per-point rendered widths in points (pressure baked in, like the
    app itself stores them)."""
    n = len(s.x)
    family = s.tool.family if s.tool else None
    default = _FAMILY_DEFAULT_WIDTH.get(family, _DEFAULT_WIDTH)
    if fidelity is Fidelity.NATIVE:
        return [default] * n
    if s.appearance is not None and s.appearance.width is not None:
        return [s.appearance.width * k] * n
    # appearance.width is None for variable-width strokes
    # (STROKED_VARIABLE, e.g. reMarkable): widths live in the channel.
    widths = s.channels.get(ir.Channel.WIDTH)
    if widths:
        return [w * k for w in widths]
    return [default] * n


def _geometry_blob(triplets: list[tuple[float, float, float]]) -> bytes:
    """(x, y, w) triplets -> tpl blob in the pressure-pen section layout
    (signature vA(v)A(u)A(u)A(v)A(v)A(u)A(u)A(u)A(u)A(v)); section 2 is
    the 3-float anchor, section 3 the path, the rest empty."""
    flat = [v for t in triplets for v in t]
    return encode_tpl([
        ("scalar", "v", [2]),
        ("array", "v", []),
        ("array", "u", list(triplets[0])),
        ("array", "u", flat),
        ("array", "v", []),
        ("array", "v", []),
        ("array", "u", []),
        ("array", "u", []),
        ("array", "u", []),
        ("array", "u", []),
        ("array", "v", []),
    ])


def _style(s: ir.Stroke) -> str:
    native = s.tool.native if s.tool else None
    if (native is not None and native.format_id == FORMAT_ID
            and str(native.tool_id) in _STYLE_FIELDS):
        return str(native.tool_id)
    return _FAMILY_STYLE.get(s.tool.family if s.tool else None, "pressure")


def _stroke_record(s: ir.Stroke, k: float, x0: float, y0: float,
                   fidelity: Fidelity, stroke_uuid: str | None = None,
                   echo: bytes | None = None, item_idx: int = 0,
                   pen_nonce: int | None = None) -> bytes:
    triplets = [
        _clamp_point((x - x0) * k, (y - y0) * k, w)
        for x, y, w in zip(s.x, s.y, _widths(s, k, fidelity))
    ]
    # The reader only recognizes triplet paths of >= 3 points (9 floats);
    # pad dots / two-point strokes by repeating the last point.
    while len(triplets) < 3:
        triplets.append(triplets[-1])

    if s.appearance is not None:
        color, opacity = s.appearance.color, s.appearance.opacity
    else:
        is_hl = s.tool is not None and s.tool.family is ir.ToolFamily.HIGHLIGHTER
        color, opacity = s.color, (0.5 if is_hl else 1.0)
    # alpha 0.0 means "omitted" on the wire (reader turns it into 1.0).
    opacity = min(max(opacity, 1 / 255), 1.0)
    color_msg = (write_float32(1, color.r) + write_float32(2, color.g)
                 + write_float32(3, color.b) + write_float32(4, opacity))

    f3, f5, is_marker = _STYLE_FIELDS[_style(s)]

    # Field 7 is an {index, nonce} identity submessage — the per-page draw
    # index plus a random u32 (the app omits index 0 on the wire; mirror
    # that). It does NOT carry the pen style (2026-07-10 finding).
    ident = write_varint_field(1, item_idx) if item_idx else b""
    if pen_nonce is not None:
        ident += write_varint_field(2, pen_nonce)

    sid = stroke_uuid or str(uuid.uuid4()).upper()
    stroke_msg = (
        write_len_delimited(1, sid.encode("ascii"))
        + write_len_delimited(2, apple_lz4_compress(_geometry_blob(triplets)))
    )
    if f3:
        stroke_msg += write_varint_field(3, f3)
    stroke_msg += write_len_delimited(4, color_msg)
    if f5:
        stroke_msg += write_varint_field(5, f5)
    if echo is not None:
        stroke_msg += write_len_delimited(6, b"")
    stroke_msg += write_len_delimited(7, write_len_delimited(1, ident))
    if echo is not None:
        # fields observed in every schema-25 app stroke record: 9 empty
        # msg, 15 = byte-exact echo of the header's field-2 version msg.
        # Version field 21 stays 24 even in schema-25 files (observed in
        # the Mac export).
        stroke_msg += (write_len_delimited(9, b"")
                       + write_len_delimited(15, echo))
    if is_marker:
        # field 20 = {1: ""} is the marker flag; every other app stroke
        # carries field 20 as EMPTY bytes (schema 25) or omits it.
        stroke_msg += write_len_delimited(20, write_len_delimited(1, b""))
    elif echo is not None:
        stroke_msg += write_len_delimited(20, b"")
    stroke_msg += write_varint_field(21, SCHEMA_VERSION)
    return write_len_delimited(7, stroke_msg)


def _reserialize(raw: bytes, overrides: dict[int, bytes]) -> bytes:
    """Re-emit a parsed message byte-faithfully, replacing the given
    length-delimited fields in place (appending any not present)."""
    import struct as _struct

    from .wire import parse_message, write_tag

    out = b""
    todo = dict(overrides)
    for f in parse_message(raw):
        if f.number in todo:
            out += write_len_delimited(f.number, todo.pop(f.number))
        elif f.wire_type == 0:
            out += write_varint_field(f.number, f.value)
        elif f.wire_type == 1:
            out += write_tag(f.number, 1) + _struct.pack("<d", f.value)
        elif f.wire_type == 2:
            out += write_len_delimited(f.number, f.value)
        elif f.wire_type == 5:
            out += write_tag(f.number, 5) + _struct.pack("<f", f.value)
    for num, value in todo.items():
        out += write_len_delimited(num, value)
    return out


def _journal_header(event_uuid: str, seq_msg: bytes, device_id: int,
                    item_idx: int, session: int) -> bytes:
    """Schema-25 page files are event journals: every payload record
    (stroke or page item) is PRECEDED by one of these header records —
    the app parses records pairwise, and an unpaired stroke record is a
    SwiftProtobuf BinaryDecodingError at import (round-3 finding).
    Fields: 1 = event UUID (the payload's stroke UUID repeats it),
    2 = version msg (echoed as the payload's field 15), 8 = device id
    (same value as the events log), 9 = per-page item index [inferred:
    unique small int], 14 = session constant, 16 = 24."""
    return (write_len_delimited(1, event_uuid.encode("ascii"))
            + write_len_delimited(2, seq_msg)
            + write_varint_field(8, device_id)
            + write_varint_field(9, item_idx)
            + write_varint_field(14, session)
            + write_varint_field(16, SCHEMA_VERSION))


def _meta_record(page_uuid: str) -> bytes:
    """Leading per-page metadata record (field 1 = UUID, field 16 =
    schema version), as observed in schema-24 samples; the reader skips
    it."""
    return (write_len_delimited(1, page_uuid.encode("ascii"))
            + write_varint_field(16, SCHEMA_VERSION))


def _thumbnail_jpeg() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buf, "JPEG")
    return buf.getvalue()


def _write_double(number: int, value: float) -> bytes:
    import struct as _struct

    from .wire import write_tag

    return write_tag(number, 1) + _struct.pack("<d", value)


def _nonce_msg(field: int, rng) -> bytes:
    """{f1: 1, f2: random u32} version-vector entry, as observed."""
    return write_len_delimited(field, write_varint_field(1, 1)
                               + write_varint_field(2, rng.getrandbits(31)))


def _events_log(doc_uuid: str, title: str, first_page_uuid: str,
                att_uuid: str, att_size: int, now_ms: int,
                device_id: int | None = None,
                page_uuids: list[str] | None = None,
                page_size_pt: tuple[float, float] = (834.24, 1078.825)) -> bytes:
    """Minimal index.events.pb: a document-created event + an
    attachment-added event, mirroring the first two records of the Mac
    export (schema decoded schema-less; field meanings [inferred]).
    Without this member GoodNotes rejects the import with "missing
    document id" — the document UUID lives ONLY here.
    """
    import random
    import struct as _struct

    rng = random.Random()
    if device_id is None:
        device_id = rng.getrandbits(62)

    created = write_len_delimited(1, doc_uuid.encode("ascii")) + \
        write_len_delimited(30, b"".join([
            write_len_delimited(1, doc_uuid.encode("ascii")),
            write_len_delimited(2, write_len_delimited(
                1, title.encode("utf-8")) + _nonce_msg(2, rng)),
            write_len_delimited(3, write_len_delimited(
                1, first_page_uuid.encode("ascii")) + _nonce_msg(2, rng)),
            write_len_delimited(6, write_len_delimited(1, b"P")
                                + _nonce_msg(2, rng)),
            write_len_delimited(7, write_len_delimited(
                1, first_page_uuid.encode("ascii")) + _nonce_msg(2, rng)),
            write_len_delimited(9, b"auto"),
            _write_double(10, float(now_ms)),
            write_len_delimited(11, str(uuid.uuid4()).upper().encode()),
            write_varint_field(13, device_id),
            write_varint_field(14, now_ms),
            write_len_delimited(17, b""),
            write_len_delimited(18, b""),
            write_len_delimited(19, _nonce_msg(2, rng)),
            write_varint_field(20, SCHEMA_VERSION),
        ]))

    attached = write_len_delimited(1, att_uuid.encode("ascii")) + \
        write_len_delimited(6, b"".join([
            write_len_delimited(1, att_uuid.encode("ascii")),
            write_len_delimited(2, att_uuid.encode("ascii")),
            write_varint_field(5, att_size),
            write_len_delimited(6, doc_uuid.encode("ascii")),
            _write_double(10, float(now_ms)),
            write_len_delimited(11, str(uuid.uuid4()).upper().encode()),
            write_len_delimited(12, write_varint_field(1, 1)
                                + write_varint_field(2, 1)),
            write_varint_field(14, device_id),
            write_varint_field(15, now_ms),
            write_varint_field(16, SCHEMA_VERSION),
        ]))
    # Page materialization (round-3 finding, 2026-07-09): without these
    # the app imports the container but shows ZERO pages — the page list
    # lives in the events journal, not index.notes.pb. Mirrors records
    # 2/3/5 of a real Mac export: a paper definition (field 2) referencing
    # the background-PDF attachment, one page-created event (field 54) per
    # page referencing the paper + a lexicographic order key, and one
    # page-link record (field 105) carrying the page's CONTENT uuid (the
    # notes/<uuid> member name).
    paper_uuid = str(uuid.uuid4()).upper()
    w_pt, h_pt = page_size_pt
    paper = write_len_delimited(1, paper_uuid.encode("ascii")) + \
        write_len_delimited(2, b"".join([
            write_len_delimited(1, doc_uuid.encode("ascii")),
            write_len_delimited(2, paper_uuid.encode("ascii")),
            write_len_delimited(4, att_uuid.encode("ascii")),
            write_varint_field(5, 1),
            write_varint_field(6, 1),
            _write_double(7, 29.33333396911621),
            write_len_delimited(8, write_float32(1, w_pt)
                                + write_float32(2, h_pt)),
            write_len_delimited(
                9, b"9FE8F365-4BEE-5057-8573-1A56C77CAC19_standard_1_"
                   b"1 - Yellow"),
            _write_double(10, float(now_ms)),
            write_len_delimited(11, str(uuid.uuid4()).upper().encode()),
            write_len_delimited(12, _write_double(1, 29.33333396911621)
                                + _nonce_msg(2, rng)),
            write_len_delimited(13, write_varint_field(1, 1)
                                + _nonce_msg(2, rng)),
            write_varint_field(15, device_id),
            write_varint_field(16, now_ms),
            write_len_delimited(17, write_varint_field(1, 1)
                                + _nonce_msg(2, rng)),
            write_len_delimited(18, b"".join([
                write_len_delimited(1, b"".join([
                    write_len_delimited(1, write_float32(1, 44.0)
                                        + write_float32(2, 58.6666679)),
                    write_len_delimited(2, write_float32(1, w_pt - 88.0)
                                        + write_float32(2, h_pt - 117.33)),
                ])),
                write_float32(2, 28.4166679),
                write_float32(3, 0.91666669),
                write_varint_field(5, 1),
            ])),
            write_len_delimited(19, _nonce_msg(2, rng)),
            write_varint_field(21, SCHEMA_VERSION),
        ]))

    records = [created, attached, paper]
    _gray = write_float32(1, 0.86666667) + write_float32(2, 0.86666667) \
        + write_float32(3, 0.86666667) + write_float32(4, 1.0)
    _white = write_float32(1, 1.0) + write_float32(2, 1.0) \
        + write_float32(3, 1.0) + write_float32(4, 1.0)
    for i, page_uuid in enumerate(page_uuids or [first_page_uuid]):
        # Page ENTITY uuid = page CONTENT uuid (the notes/<uuid> member
        # name) minus one — the app allocates them adjacently and links
        # entity -> content by this adjacency [inferred: single Mac
        # export sample ...F0955F entity / ...F09560 content; confirmed
        # by import behavior — random entity uuids leave the page blank].
        head, tail = page_uuid.rsplit("-", 1)
        entity_uuid = f"{head}-{int(tail, 16) - 1:012X}"
        order_key = f"43el{chr(ord('Q') + i)}2"  # lexicographic page order
        page_created = write_len_delimited(1, entity_uuid.encode("ascii")) + \
            write_len_delimited(54, b"".join([
                write_len_delimited(1, doc_uuid.encode("ascii")),
                write_len_delimited(2, entity_uuid.encode("ascii")),
                write_len_delimited(3, write_len_delimited(
                    1, paper_uuid.encode("ascii")) + _nonce_msg(2, rng)),
                write_len_delimited(4, write_len_delimited(
                    1, order_key.encode("ascii")) + _nonce_msg(2, rng)),
                _write_double(10, float(now_ms)),
                write_len_delimited(11, str(uuid.uuid4()).upper().encode()),
                write_varint_field(13, device_id),
                write_varint_field(14, now_ms),
                write_varint_field(15, SCHEMA_VERSION),
                write_len_delimited(17, write_len_delimited(
                    1, write_len_delimited(
                        2, write_len_delimited(1, _gray)
                        + write_len_delimited(2, _white)))
                    + _nonce_msg(2, rng)),
            ]))
        page_link = write_len_delimited(1, page_uuid.encode("ascii")) + \
            write_len_delimited(105, b"".join([
                write_varint_field(1, i + 1),
                write_len_delimited(2, doc_uuid.encode("ascii")),
                write_len_delimited(4, page_uuid.encode("ascii")),
                write_len_delimited(6, b"auto"),
                _write_double(10, float(now_ms)),
                write_len_delimited(11, str(uuid.uuid4()).upper().encode()),
                write_varint_field(13, device_id),
                write_varint_field(14, now_ms),
                write_varint_field(15, SCHEMA_VERSION),
            ]))
        records += [page_created, page_link]
    return join_delimited(records)


def _blank_pdf(width_pt: float, height_pt: float) -> bytes:
    """Blank one-page PDF, the shape GoodNotes stores paper backgrounds
    in as `attachments/<uuid>`."""
    from reportlab.pdfgen import canvas as pdfcanvas

    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=(width_pt, height_pt))
    c.showPage()
    c.save()
    return buf.getvalue()


def document_to_goodnotes(doc: ir.Document,
                          fidelity: Fidelity = Fidelity.EXACT) -> bytes:
    if fidelity is Fidelity.RAW:
        raise ValueError(
            "goodnotes stores device-rendered widths, not raw dynamics; "
            "use exact or native fidelity"
        )
    import random as _random

    # schema.pb must match the page-file structure: schema-24 page files
    # are flat (meta record + stroke records), schema-25 files are event
    # JOURNALS of (header, payload) record pairs. Writing 25-shaped
    # records under a 24 schema.pb (or unpaired stroke records under 25)
    # is a SwiftProtobuf BinaryDecodingError at import — round-3 finding,
    # 2026-07-09. Per-record version fields stay 24 in BOTH shapes (the
    # real schema-25 Mac export still writes 24 there).
    schema = doc.extra.get("goodnotes", {}).get("schema_version",
                                                SCHEMA_VERSION)
    journal = schema >= 25
    rng = _random.Random()
    device_id = rng.getrandbits(62)
    session = 5381  # constant observed across all records of an export
    pages: list[tuple[str, bytes]] = []
    for page in doc.pages:
        k = unit_factor(page, 1.0)  # GoodNotes units ARE PDF points
        b = page.bounds
        gn_extra = page.extra.get("goodnotes", {})
        page_uuid = gn_extra.get("page_uuid") or str(uuid.uuid4()).upper()
        meta_hex = gn_extra.get("meta_record")
        pair_hex = gn_extra.get("meta_payload")
        strokes = [s for layer in page.layers if layer.visible
                   for s in layer.strokes if len(s.x) >= 1]
        if journal:
            records = []
            if meta_hex:
                records.append(bytes.fromhex(meta_hex))
                if pair_hex:
                    records.append(bytes.fromhex(pair_hex))
            for i, s in enumerate(strokes):
                event_uuid = str(uuid.uuid4()).upper()
                seq_msg = (write_varint_field(1, 2)
                           + write_varint_field(2, rng.getrandbits(31)))
                records.append(_journal_header(
                    event_uuid, seq_msg, device_id, i + 10, session))
                raw_hex = s.extra.get(FORMAT_ID, {}).get("record")
                if raw_hex:
                    # byte-faithful replay: only the journal linkage
                    # fields (stroke uuid = header event uuid, field-15
                    # echo of the header's version msg) are rewritten
                    records.append(write_len_delimited(7, _reserialize(
                        bytes.fromhex(raw_hex),
                        {1: event_uuid.encode("ascii"), 15: seq_msg})))
                else:
                    records.append(_stroke_record(
                        s, k, b.x_min, b.y_min, fidelity,
                        stroke_uuid=event_uuid, echo=seq_msg,
                        item_idx=i + 9,  # header idx (i+10) minus one,
                        # matching the observed app relation
                        pen_nonce=rng.getrandbits(31)))
        else:
            records = [bytes.fromhex(meta_hex) if meta_hex
                       else _meta_record(page_uuid)]
            records += [_stroke_record(s, k, b.x_min, b.y_min, fidelity,
                                       item_idx=i)
                        for i, s in enumerate(strokes)]
        pages.append((page_uuid, join_delimited(records)))

    def _index(entries: list[tuple[str, str, bool]]) -> bytes:
        return join_delimited([
            write_len_delimited(1, u.encode("ascii"))
            + write_len_delimited(2, p.encode("ascii"))
            + (write_varint_field(3, 1) if flag else b"")
            for u, p, flag in entries
        ])

    att_uuid = str(uuid.uuid4()).upper()
    doc_uuid = str(uuid.uuid4()).upper()
    first = doc.pages[0].bounds if doc.pages else None
    page_w = first.width if first else 595.0
    page_h = first.height if first else 842.0
    pdf = _blank_pdf(page_w, page_h)
    import time as _time

    events = _events_log(doc_uuid, doc.title or "inkterop export",
                         pages[0][0] if pages else str(uuid.uuid4()).upper(),
                         att_uuid, len(pdf), int(_time.time() * 1000),
                         device_id, page_uuids=[u for u, _ in pages],
                         page_size_pt=(page_w, page_h))

    buf = io.BytesIO()
    # member order mirrors the observed Mac export
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("document.info.pb", b"")
        zf.writestr("index.search.pb",
                    _index([(att_uuid, f"search/{att_uuid}", True)]))
        zf.writestr("index.notes.pb",
                    _index([(u, f"notes/{u}", False) for u, _ in pages]))
        # minimal search record as observed: {2: 1, 3: ""}
        zf.writestr(f"search/{att_uuid}",
                    join_delimited([write_varint_field(2, 1)
                                    + write_len_delimited(3, b"")]))
        for page_uuid, data in pages:
            zf.writestr(f"notes/{page_uuid}", data)
        zf.writestr("index.events.pb", events)
        zf.writestr("thumbnail.jpg", _thumbnail_jpeg())
        zf.writestr("index.attachments.pb",
                    _index([(att_uuid, f"attachments/{att_uuid}", False)]))
        zf.writestr(f"attachments/{att_uuid}", pdf)
        zf.writestr("schema.pb", write_varint_field(1, schema))
    return buf.getvalue()


class GoodnotesWriter:
    format_id = FORMAT_ID
    extensions = (".goodnotes",)
    validated = False  # pending GoodNotes Mac app-import check

    def write(self, doc: ir.Document, path: Path, fidelity: Fidelity,
              options: dict[str, Any] | None = None) -> None:
        path.write_bytes(document_to_goodnotes(doc, fidelity))
