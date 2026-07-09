"""OneNote ``.one`` reader tests.

Unit tests run against synthetic MS-ONESTORE bytes built here; the
integration tests run against the onenote.rs sample corpus
(corpus/third-party/onenote.rs, gitignored) and skip when it is absent.
"""
from __future__ import annotations

import struct
import uuid
from pathlib import Path

import pytest

from inkterop import ir
from inkterop.formats.base import Fidelity
from inkterop.formats.isf import write_mbsint, write_mbuint
from inkterop.formats.onenote import OneNoteReader
from inkterop.formats.onenote import onestore
from inkterop.formats.onenote.onestore import (
    ChunkRef,
    OneStoreError,
    parse_object_prop_set,
    parse_onestore,
    read_file_node_chunk_ref,
)
from inkterop.formats.onenote.reader import (
    DIM_PRESSURE,
    DIM_X,
    DIM_Y,
    decode_ink_path,
    read_one,
)


def _find_corpus() -> Path:
    """corpus/ lives at the repo root, gitignored; in linked worktrees it
    only exists in the main checkout, so walk up the ancestors."""
    for base in Path(__file__).resolve().parents[2:]:
        candidate = base / "corpus" / "third-party" / "onenote.rs" / \
            "crates" / "parser" / "tests" / "samples"
        if candidate.is_dir():
            return candidate
    return Path(__file__).parents[2] / "corpus" / "third-party" / \
        "onenote.rs" / "crates" / "parser" / "tests" / "samples"


CORPUS = _find_corpus()
needs_corpus = pytest.mark.skipif(
    not CORPUS.is_dir(), reason="onenote.rs sample corpus not present")

FIXTURES = Path(__file__).parent / "fixtures"


# ------------------------------------------------------------ helpers/builders

def _guid_bytes(g: str) -> bytes:
    return uuid.UUID(g).bytes_le


def _exguid(g: str, n: int) -> bytes:
    return _guid_bytes(g) + struct.pack("<I", n)


G_SPACE = "11111111-1111-1111-1111-111111111111"
G_REV = "22222222-2222-2222-2222-222222222222"
G_OBJ = "33333333-3333-3333-3333-333333333333"


def _ink_path_bytes(deltas: list[int]) -> bytes:
    out = bytearray(write_mbuint(len(deltas) << 1))
    for d in deltas:
        out += write_mbsint(d)
    return bytes(out)


def _prop_set(props: list[tuple[int, int, bytes]],
              oids: list[int]) -> bytes:
    """props: (26-bit id, type, encoded payload). Emits an OID stream and
    marks the OSID stream absent."""
    out = bytearray(struct.pack("<I", len(oids) | (1 << 31)))
    for cid in oids:
        out += struct.pack("<I", cid)
    out += struct.pack("<H", len(props))
    for pid, ptype, _payload in props:
        out += struct.pack("<I", (pid & 0x3FFFFFF) | (ptype << 26))
    for _pid, _ptype, payload in props:
        out += payload
    return bytes(out)


def _dim_entry(guid: str, lower: int, upper: int) -> bytes:
    return _guid_bytes(guid) + struct.pack("<iiIf", lower, upper, 2, 1000.0)


class _StoreBuilder:
    """Assembles a minimal classic revision store byte-for-byte."""

    def __init__(self):
        self.buf = bytearray(b"\0" * 1024)  # header patched at the end

    def add(self, data: bytes) -> ChunkRef:
        stp = len(self.buf)
        self.buf += data
        return ChunkRef(stp, len(data))

    @staticmethod
    def node(node_id: int, payload: bytes = b"",
             ref: ChunkRef | None = None, base_type: int = 0) -> bytes:
        body = b""
        if ref is not None:
            body += struct.pack("<QI", ref.stp, ref.cb)  # stpFormat/cbFormat 0
        body += payload
        size = 4 + len(body)
        header = (node_id | (size << 10) | (base_type << 27))
        return struct.pack("<I", header) + body

    def node_list(self, list_id: int, nodes: list[bytes]) -> ChunkRef:
        body = b"".join(nodes)
        frag = bytearray()
        frag += struct.pack("<QII", onestore.FRAGMENT_MAGIC, list_id, 0)
        frag += body
        frag += struct.pack("<QI", 0xFFFFFFFFFFFFFFFF, 0)  # nil next fragment
        frag += struct.pack("<Q", onestore.FRAGMENT_FOOTER)
        return self.add(bytes(frag))


def build_minimal_one(ink_deltas: list[int], with_pressure: bool = True,
                      raster_op: int | None = None) -> bytes:
    """One page, one InkContainer -> InkDataNode -> one stroke."""
    b = _StoreBuilder()
    f32 = lambda v: struct.pack("<f", v)  # noqa: E731

    dims = _dim_entry(DIM_X, -(2 ** 31), 2 ** 31 - 1) + \
        _dim_entry(DIM_Y, -(2 ** 31), 2 ** 31 - 1)
    if with_pressure:
        dims += _dim_entry(DIM_PRESSURE, 0, 32767)
    props_entries = [
        (0x340A, 0x7, struct.pack("<I", len(dims)) + dims),  # InkDimensions
        (0x340F, 0x5, struct.pack("<I", 0x00FF0000)),        # InkColor: blue
        (0x340C, 0x5, f32(100.0)),                           # InkHeight
        (0x340D, 0x5, f32(100.0)),                           # InkWidth
    ]
    if raster_op is not None:
        props_entries.append((0x3413, 0x3, bytes([raster_op])))
    stroke_props_blob = b.add(_prop_set(props_entries, []))

    path = _ink_path_bytes(ink_deltas)
    stroke_blob = b.add(_prop_set([
        (0x340B, 0x7, struct.pack("<I", len(path)) + path),  # InkPath
        (0x3409, 0x8, b""),                                  # props ref
    ], [(0 << 8) | 6]))  # CompactId -> guidIndex 0, n 6 (stroke props)

    ink_data_blob = b.add(_prop_set([
        (0x3416, 0x9, struct.pack("<I", 1)),  # InkStrokes, 1 element
    ], [(0 << 8) | 5]))  # -> stroke node

    container_blob = b.add(_prop_set([
        (0x1C14, 0x5, f32(1.0)),   # OffsetFromParentHoriz (half-inch)
        (0x1C15, 0x5, f32(2.0)),   # OffsetFromParentVert
        (0x3415, 0x8, b""),        # InkData ref
        (0x1C46, 0x5, f32(1.0)),   # InkScalingX
        (0x1C47, 0x5, f32(1.0)),   # InkScalingY
    ], [(0 << 8) | 4]))  # -> ink data node

    title = "Synthetic page".encode("utf-16-le")
    page_blob = b.add(_prop_set([
        (0x1C01, 0x5, f32(17.0)),  # PageWidth (half-inch)
        (0x1C02, 0x5, f32(22.0)),  # PageHeight
        (0x1D3C, 0x7, struct.pack("<I", len(title)) + title),
        (0x1C20, 0x9, struct.pack("<I", 1)),  # ElementChildNodes
    ], [(0 << 8) | 3]))  # -> ink container

    def decl(n: int, jcid: int, blob: ChunkRef) -> bytes:
        payload = struct.pack("<IIBB", (0 << 8) | n, jcid, 0x3, 1)
        return _StoreBuilder.node(onestore.FN_OBJECT_DECL2_REF_COUNT,
                                  payload, ref=blob, base_type=1)

    group_nodes = [
        _StoreBuilder.node(onestore.FN_OBJECT_GROUP_START,
                           _exguid(G_OBJ, 99)),
        _StoreBuilder.node(onestore.FN_GLOBAL_ID_TABLE_START2),
        _StoreBuilder.node(onestore.FN_GLOBAL_ID_TABLE_ENTRY,
                           struct.pack("<I", 0) + _guid_bytes(G_OBJ)),
        _StoreBuilder.node(onestore.FN_GLOBAL_ID_TABLE_END),
        decl(2, 0x0006000B, page_blob),        # jcidPageNode
        decl(3, 0x00060014, container_blob),   # jcidInkContainer
        decl(4, 0x0002003B, ink_data_blob),    # jcidInkDataNode
        decl(5, 0x00020047, stroke_blob),      # jcidInkStrokeNode
        decl(6, 0x00120048, stroke_props_blob),
        _StoreBuilder.node(onestore.FN_OBJECT_GROUP_END),
    ]
    group_list = b.node_list(0x13, group_nodes)

    rev_nodes = [
        _StoreBuilder.node(onestore.FN_REVISION_MANIFEST_LIST_START,
                           _exguid(G_SPACE, 1) + struct.pack("<I", 0)),
        _StoreBuilder.node(onestore.FN_REVISION_MANIFEST_START6,
                           _exguid(G_REV, 1) + _exguid(G_REV, 0)
                           + struct.pack("<IH", 1, 0)),
        _StoreBuilder.node(onestore.FN_OBJECT_GROUP_LIST_REF,
                           _exguid(G_REV, 7), ref=group_list, base_type=2),
        _StoreBuilder.node(onestore.FN_ROOT_OBJECT_REFERENCE3,
                           _exguid(G_OBJ, 2) + struct.pack("<I", 1)),
        _StoreBuilder.node(onestore.FN_REVISION_MANIFEST_END),
    ]
    rev_list = b.node_list(0x12, rev_nodes)

    manifest_nodes = [
        _StoreBuilder.node(onestore.FN_OBJECT_SPACE_MANIFEST_LIST_START,
                           _exguid(G_SPACE, 1)),
        _StoreBuilder.node(onestore.FN_REVISION_MANIFEST_LIST_REF,
                           ref=rev_list, base_type=2),
    ]
    manifest_list = b.node_list(0x11, manifest_nodes)

    root_nodes = [
        _StoreBuilder.node(onestore.FN_OBJECT_SPACE_MANIFEST_ROOT,
                           _exguid(G_SPACE, 1)),
        _StoreBuilder.node(onestore.FN_OBJECT_SPACE_MANIFEST_LIST_REF,
                           _exguid(G_SPACE, 1), ref=manifest_list,
                           base_type=2),
    ]
    root_list = b.node_list(0x10, root_nodes)

    counts = {0x10: 2, 0x11: 2, 0x12: 5, 0x13: 10}
    log = bytearray()
    for src, count in counts.items():
        log += struct.pack("<II", src, count)
    log += struct.pack("<II", 1, 0xDEADBEEF)  # sentinel (crc)
    log += struct.pack("<QI", 0xFFFFFFFFFFFFFFFF, 0)  # nil next fragment
    txn_log = b.add(bytes(log))

    hdr = b.buf
    hdr[0:16] = _guid_bytes(onestore.GUID_FILE_TYPE_ONE)
    hdr[48:64] = _guid_bytes(onestore.GUID_FILE_FORMAT_CLASSIC)
    struct.pack_into("<I", hdr, 96, 1)  # cTransactionsInLog
    struct.pack_into("<QI", hdr, 160, txn_log.stp, txn_log.cb)
    struct.pack_into("<QI", hdr, 172, root_list.stp, root_list.cb)
    return bytes(hdr)


# ---------------------------------------------------------------- unit tests

def test_chunk_ref_formats():
    # stpFormat 2 (u16 * 8), cbFormat 2 (u8 * 8)
    ref, pos = read_file_node_chunk_ref(struct.pack("<HB", 100, 3), 0, 2, 2)
    assert (ref.stp, ref.cb) == (800, 24)
    assert pos == 3
    # stpFormat 1 (u32), cbFormat 0 (u32)
    ref, pos = read_file_node_chunk_ref(struct.pack("<II", 7, 9), 0, 1, 0)
    assert (ref.stp, ref.cb) == (7, 9)


def test_prop_set_parse_and_oid_order():
    dims = b"\xAB" * 8
    blob = _prop_set([
        (0x0001, 0x5, struct.pack("<I", 42)),      # u32
        (0x0002, 0x8, b""),                        # ObjectID (oid #0)
        (0x0003, 0x9, struct.pack("<I", 2)),       # array (oids #1, #2)
        (0x0004, 0x8, b""),                        # ObjectID (oid #3)
        (0x0005, 0x7, struct.pack("<I", 8) + dims),  # data
        (0x0006, 0x2, b""),                        # bool (value in id bit)
    ], oids=[(0 << 8) | 10, (0 << 8) | 11, (0 << 8) | 12, (0 << 8) | 13])
    parsed = parse_object_prop_set(blob)
    assert parsed.props.get(0x0001).value == 42
    assert parsed.props.get(0x0005).value == dims
    assert parsed.props.get(0x0006).value is False
    assert len(parsed.oids) == 4

    obj = onestore.OneObject(
        jcid=0, props=parsed.props,
        oids=onestore._compact_ids(parsed.oids, {0: G_OBJ}, "test"))
    assert obj.ref_at(0x0002) == (G_OBJ, 10)
    assert obj.ref_array_at(0x0003) == [(G_OBJ, 11), (G_OBJ, 12)]
    assert obj.ref_at(0x0004) == (G_OBJ, 13)


def test_prop_set_bool_true_bit():
    blob = _prop_set([(0x0006 | (1 << 5), 0x2, b"")], oids=[])
    # bit 31 of the PropertyID carries the bool value
    raw = bytearray(blob)
    raw[4 + 2 + 3] |= 0x80  # set top bit of the (only) PropertyID
    parsed = parse_object_prop_set(bytes(raw))
    assert parsed.props.entries[0].value is True


def test_decode_ink_path_roundtrip():
    deltas = [1000, -3, 7, 0, -128, 129, 2 ** 20, -(2 ** 20)]
    assert decode_ink_path(_ink_path_bytes(deltas)) == deltas


def test_synthetic_store_parses():
    data = build_minimal_one([100, 1, 1, 200, 2, 2, 16000, 0, -100])
    store = parse_onestore(data)
    assert len(store.object_spaces) == 1
    space = store.object_spaces[0]
    assert store.root_gosid == (G_SPACE, 1)
    assert len(space.objects) == 5
    jcids = sorted(o.jcid for o in space.objects.values())
    assert jcids == [0x0002003B, 0x00020047, 0x0006000B, 0x00060014,
                     0x00120048]
    assert space.roots[1] == (G_OBJ, 2)


def test_synthetic_ink_document():
    # 3 points x 3 dims: x deltas, y deltas, pressure deltas
    data = build_minimal_one([100, 10, 10,       # x: 100, 110, 120
                              200, 0, 20,        # y: 200, 200, 220
                              16384, 0, 8191])   # pressure
    doc = read_one(data, title="synthetic")
    doc.validate()
    assert len(doc.pages) == 1
    page = doc.pages[0]
    assert doc.metadata["page_titles"] == ["Synthetic page"]
    strokes = list(page.strokes())
    assert len(strokes) == 1
    s = strokes[0]
    # x = 1 half-inch offset + himetric coords: 36 + 100 * 72/2540
    assert s.x[0] == pytest.approx(36 + 100 * 72 / 2540)
    assert s.x[2] == pytest.approx(36 + 120 * 72 / 2540)
    assert s.y[0] == pytest.approx(72 + 200 * 72 / 2540)
    pressures = s.channels[ir.Channel.PRESSURE]
    assert pressures[0] == pytest.approx(16384 / 32767)
    assert pressures[2] == pytest.approx(24575 / 32767)
    assert s.color.b == pytest.approx(1.0)  # COLORREF 0x00FF0000 = blue
    assert s.color.r == pytest.approx(0.0)
    assert s.appearance.width == pytest.approx(100 * 72 / 2540)
    # page bounds: 17 x 22 half-inch
    assert page.bounds.width == pytest.approx(17 * 36)
    assert page.bounds.height == pytest.approx(22 * 36)


def test_synthetic_highlighter():
    data = build_minimal_one([0, 10, 0, 10, 16000, 0], with_pressure=False,
                             raster_op=9)
    doc = read_one(data)
    (s,) = list(doc.pages[0].strokes())
    assert s.tool.family is ir.ToolFamily.HIGHLIGHTER
    assert s.appearance.underlay is True
    assert ir.Channel.PRESSURE not in s.channels


def test_truncated_file_rejected():
    with pytest.raises(OneStoreError):
        parse_onestore(b"\x00" * 100)
    bad = bytearray(build_minimal_one([0, 0]))
    bad[0] ^= 0xFF
    with pytest.raises(OneStoreError):
        parse_onestore(bytes(bad))


# ------------------------------------------------------------------- detect()

def test_detect_synthetic(tmp_path):
    f = tmp_path / "synthetic.one"
    f.write_bytes(build_minimal_one([0, 0]))
    assert OneNoteReader().detect(f)


def test_detect_rejects_other_formats():
    reader = OneNoteReader()
    for rel in ("isf/pen-pressure-tilt.isf", "uim/two-strokes-pressure.uim",
                "saber/saber-mac-pens-text.sba",
                "remarkable/ballpoint-small.rm"):
        fixture = FIXTURES / rel
        assert fixture.exists(), fixture
        assert not reader.detect(fixture)


# -------------------------------------------------------------- corpus tests

@needs_corpus
def test_detect_corpus_samples():
    reader = OneNoteReader()
    assert reader.detect(CORPUS / "joplin" / "scaled_ink.one")
    assert reader.detect(CORPUS / "handwriting_recognition.one")


@needs_corpus
def test_scaled_ink():
    doc = OneNoteReader().read(CORPUS / "joplin" / "scaled_ink.one")
    doc.validate()
    assert len(doc.pages) == 1
    page = doc.pages[0]
    strokes = list(page.strokes())
    assert len(strokes) == 2

    # both strokes carry a pressure channel
    for s in strokes:
        p = s.channels[ir.Channel.PRESSURE]
        assert len(p) == len(s.x)
        assert all(0.0 <= v <= 1.0 for v in p)

    # plausible geometry: ink inside the page, mm-scale extents.
    # observed: ink bbox ~ (98,110)-(234,474) pt on a 754x783 pt page,
    # only under offset*36 + cumsum(deltas)*scaling*72/2540 -- the
    # InkScalingY=7.18 / OffsetVert=-19.03 container lands next to the
    # unscaled one.
    xs = [x for s in strokes for x in s.x]
    ys = [y for s in strokes for y in s.y]
    assert 0 <= min(xs) and max(xs) <= page.bounds.width
    assert 0 <= min(ys) and max(ys) <= page.bounds.height
    assert 50 < max(xs) - min(xs) < 400
    assert 100 < max(ys) - min(ys) < 700

    # one red-ish stroke, one black (COLORREF decode)
    reds = sorted(round(s.color.r, 2) for s in strokes)
    assert reds[0] == 0.0 and reds[1] > 0.85


@needs_corpus
def test_handwriting_recognition_sample():
    doc = OneNoteReader().read(CORPUS / "handwriting_recognition.one")
    doc.validate()
    strokes = [s for p in doc.pages for s in p.strokes()]
    assert len(strokes) == 62
    for s in strokes:
        assert len(s.x) >= 1


@needs_corpus
def test_desktop_missing_ink_graceful():
    """This file contains InkDataNodes with no strokes (named for the bug
    it reproduces); it must read cleanly, not crash."""
    doc = OneNoteReader().read(CORPUS / "joplin" / "desktop_missing_ink.one")
    doc.validate()
    assert len(doc.pages) == 2
    assert sum(1 for p in doc.pages for _ in p.strokes()) >= 1


@needs_corpus
@pytest.mark.parametrize("name", ["joplin/Math.one",
                                  "joplin/checkboxes_and_unicode.one"])
def test_non_ink_files_read_to_zero_strokes(name):
    doc = OneNoteReader().read(CORPUS / Path(name))
    doc.validate()
    assert sum(1 for p in doc.pages for _ in p.strokes()) == 0
    assert len(doc.pages) >= 1


@needs_corpus
def test_fsshttpb_rejected_with_clear_error():
    path = CORPUS / "joplin" / "new_section.one"
    assert OneNoteReader().detect(path)  # it IS a OneNote file
    with pytest.raises(OneStoreError, match="not supported"):
        OneNoteReader().read(path)


@needs_corpus
def test_scaled_ink_to_pdf(tmp_path):
    from inkterop.render.pdf import PdfWriter
    doc = OneNoteReader().read(CORPUS / "joplin" / "scaled_ink.one")
    out = tmp_path / "scaled_ink.pdf"
    PdfWriter().write(doc, out, Fidelity.EXACT)
    assert out.stat().st_size > 1000
    assert out.read_bytes()[:5] == b"%PDF-"
