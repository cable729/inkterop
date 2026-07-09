"""MS-ONESTORE revision-store container parser (the OneNote 2016 ``.one``
on-disk format).

Implements the CLASSIC revision store only ([MS-ONESTORE] sections 2.1-2.6,
published under the Microsoft Open Specification Promise). The alternative
FSSHTTPB packaging (cloud-synced files, ``guidFileFormat``
{638DE92F-A6D4-4BC1-9A36-B3FC2511A5B7}) is detected and rejected upstream.

Layer map (all little-endian) [verified: MS-ONESTORE + samples]:

  Header (1024 bytes @ 0): guidFileType {7B5C52E4-D88C-4DA7-AEB1-5378D02996D3}
    (.one) / {43FF2FA1-EFD9-4C76-9EE2-10EA5722765F} (.onetoc2), guidFileFormat
    {109ADD3F-911B-49F5-A5D0-1791EDC8AED8} @ 48, cTransactionsInLog @ 96,
    fcrTransactionLog @ 160 and fcrFileNodeListRoot @ 172 (both
    FileChunkReference64x32 = u64 stp + u32 cb).

  Transaction log: fragment chain; each fragment holds (cb-12)/8 entries of
    (srcID u32, TransactionEntrySwitch u32) + a 12-byte next-fragment
    reference. Entries between sentinels (srcID == 1) form one transaction;
    an entry declares the CUMULATIVE FileNode count of FileNodeList ``srcID``.
    Only the first cTransactionsInLog transactions are committed; the last
    committed value per list bounds how many nodes of that list are valid.

  FileNodeListFragment: magic u64 0xA4567AB1F5F7F4C4 + FileNodeListID u32 +
    nFragmentSequence u32 (16 bytes), FileNode stream, padding, nextFragment
    (FileChunkReference64x32), footer u64 0x8BC215C38233BA4B.

  FileNode header u32: FileNodeID bits 0-9, Size bits 10-22 (whole node incl.
    header), StpFormat bits 23-24 (0:u64 1:u32 2:u16*8 3:u32*8), CbFormat
    bits 25-26 (0:u32 1:u64 2:u8*8 3:u16*8), BaseType bits 27-30 (0 no ref,
    1 ref to data, 2 ref to a child FileNodeList).

  ObjectSpaceObjectPropSet ([MS-ONESTORE] 2.6.1): OID stream (header u32:
    count bits 0-23, ExtendedStreamsPresent bit 30, OsidStreamNotPresent
    bit 31; then count CompactIds), optional OSID stream, optional ContextID
    stream, then the PropertySet: u16 count, count PropertyID u32s (id bits
    0-25, type bits 26-30, bool-value bit 31), then the values back to back.
    ObjectID-typed properties consume CompactIds from the OID stream in
    property order (arrays consume ``count``, nested property sets recurse).

  CompactId u32 = n (bits 0-7) + guidIndex (bits 8-31); resolved against the
  revision's global ID table (GlobalIdTableEntryFNDX index -> GUID) to an
  ExtendedGUID (guid, n).

Revision semantics: object spaces come from the root file node list; each
ObjectSpaceManifestListReferenceFND names one object space, whose manifest
list must end in the CURRENT RevisionManifestListReferenceFND ([MS-ONESTORE]
2.1.6: all but the last are ignored). Within the revision manifest list we
replay every revision in file order into one object map (later declarations
shadow earlier ones), which yields the newest state without materializing
the history [inferred: same strategy onenote.rs uses; the spec's dependency
chain would allow proper time travel].

Multibyte integer primitives are shared with the ISF reader (the OneNote ink
stream uses the ISF encodings; see reader.py).
"""
from __future__ import annotations

import logging
import struct
import uuid
from dataclasses import dataclass, field

_logger = logging.getLogger(__name__)


class OneStoreError(ValueError):
    pass


# ------------------------------------------------------------------ GUIDs

def read_guid(buf: bytes, pos: int) -> str:
    """16 little-endian GUID bytes -> lowercase canonical string."""
    if pos + 16 > len(buf):
        raise OneStoreError("GUID past end of buffer")
    return str(uuid.UUID(bytes_le=bytes(buf[pos:pos + 16])))


GUID_FILE_TYPE_ONE = "7b5c52e4-d88c-4da7-aeb1-5378d02996d3"
GUID_FILE_TYPE_TOC2 = "43ff2fa1-efd9-4c76-9ee2-10ea5722765f"
GUID_FILE_FORMAT_CLASSIC = "109add3f-911b-49f5-a5d0-1791edc8aed8"
GUID_FILE_FORMAT_FSSHTTPB = "638de92f-a6d4-4bc1-9a36-b3fc2511a5b7"

#: ExtendedGUID: (guid string, n). GUID nil + n 0 == extended-GUID zero.
ExGuid = tuple[str, int]

FRAGMENT_MAGIC = 0xA4567AB1F5F7F4C4
FRAGMENT_FOOTER = 0x8BC215C38233BA4B

# FileNodeID values ([MS-ONESTORE] 2.4.3 table)
FN_OBJECT_SPACE_MANIFEST_ROOT = 0x004
FN_OBJECT_SPACE_MANIFEST_LIST_REF = 0x008
FN_OBJECT_SPACE_MANIFEST_LIST_START = 0x00C
FN_REVISION_MANIFEST_LIST_REF = 0x010
FN_REVISION_MANIFEST_LIST_START = 0x014
FN_REVISION_MANIFEST_START4 = 0x01B
FN_REVISION_MANIFEST_END = 0x01C
FN_REVISION_MANIFEST_START6 = 0x01E
FN_REVISION_MANIFEST_START7 = 0x01F
FN_GLOBAL_ID_TABLE_START = 0x021
FN_GLOBAL_ID_TABLE_START2 = 0x022
FN_GLOBAL_ID_TABLE_ENTRY = 0x024
FN_GLOBAL_ID_TABLE_ENTRY2 = 0x025
FN_GLOBAL_ID_TABLE_ENTRY3 = 0x026
FN_GLOBAL_ID_TABLE_END = 0x028
FN_OBJECT_DECL_WITH_REF_COUNT = 0x02D
FN_OBJECT_DECL_WITH_REF_COUNT2 = 0x02E
FN_OBJECT_REVISION_WITH_REF_COUNT = 0x041
FN_OBJECT_REVISION_WITH_REF_COUNT2 = 0x042
FN_ROOT_OBJECT_REFERENCE2 = 0x059
FN_ROOT_OBJECT_REFERENCE3 = 0x05A
FN_REVISION_ROLE_DECLARATION = 0x05C
FN_REVISION_ROLE_AND_CONTEXT = 0x05D
FN_OBJECT_DECL_FILE_DATA3 = 0x072
FN_OBJECT_DECL_FILE_DATA3_LARGE = 0x073
FN_OBJECT_DATA_ENCRYPTION_KEY = 0x07C
FN_OBJECT_INFO_DEPENDENCY_OVERRIDES = 0x084
FN_DATA_SIGNATURE_GROUP = 0x08C
FN_FILE_DATA_STORE_LIST_REF = 0x090
FN_FILE_DATA_STORE_OBJECT_REF = 0x094
FN_OBJECT_DECL2_REF_COUNT = 0x0A4
FN_OBJECT_DECL2_LARGE_REF_COUNT = 0x0A5
FN_OBJECT_GROUP_LIST_REF = 0x0B0
FN_OBJECT_GROUP_START = 0x0B4
FN_OBJECT_GROUP_END = 0x0B8
FN_HASHED_CHUNK_DESCRIPTOR2 = 0x0C2
FN_READONLY_OBJECT_DECL2_REF_COUNT = 0x0C4
FN_READONLY_OBJECT_DECL2_LARGE = 0x0C5
FN_CHUNK_TERMINATOR = 0x0FF

# Root roles ([MS-ONE] 2.1.8)
ROLE_DEFAULT_CONTENT = 1
ROLE_METADATA = 2
ROLE_VERSION_METADATA = 4

# Property types ([MS-ONESTORE] 2.6.6)
PT_EMPTY = 0x1
PT_BOOL = 0x2
PT_U8 = 0x3
PT_U16 = 0x4
PT_U32 = 0x5
PT_U64 = 0x6
PT_DATA = 0x7  # prtFourBytesOfLengthFollowedByData
PT_OID = 0x8
PT_OID_ARRAY = 0x9
PT_OSID = 0xA
PT_OSID_ARRAY = 0xB
PT_CTXID = 0xC
PT_CTXID_ARRAY = 0xD
PT_PROPERTY_VALUES = 0x10  # prtArrayOfPropertyValues
PT_PROPERTY_SET = 0x11


# ---------------------------------------------------------- low-level reads

def _u16(buf, pos): return struct.unpack_from("<H", buf, pos)[0]
def _u32(buf, pos): return struct.unpack_from("<I", buf, pos)[0]
def _u64(buf, pos): return struct.unpack_from("<Q", buf, pos)[0]


def _check(buf, pos, need, what):
    if pos + need > len(buf):
        raise OneStoreError(f"truncated {what} at offset {pos}")


@dataclass(frozen=True)
class ChunkRef:
    stp: int
    cb: int

    @property
    def is_nil(self) -> bool:
        # all stp bits set (in its serialized width) + cb == 0; the parse
        # normalizes 8x-multiplied formats, so compare against the max
        return self.cb == 0 and self.stp in (
            0xFFFFFFFFFFFFFFFF, 0xFFFFFFFF, 0xFFFF * 8, 0xFFFFFFFF * 8)

    @property
    def is_zero(self) -> bool:
        return self.stp == 0 and self.cb == 0


def read_fcr64x32(buf, pos) -> tuple[ChunkRef, int]:
    _check(buf, pos, 12, "FileChunkReference64x32")
    return ChunkRef(_u64(buf, pos), _u32(buf, pos + 8)), pos + 12


_STP_FMT = {0: ("<Q", 1), 1: ("<I", 1), 2: ("<H", 8), 3: ("<I", 8)}
_CB_FMT = {0: ("<I", 1), 1: ("<Q", 1), 2: ("<B", 8), 3: ("<H", 8)}


def read_file_node_chunk_ref(buf, pos, stp_fmt, cb_fmt) -> tuple[ChunkRef, int]:
    """FileNodeChunkReference ([MS-ONESTORE] 2.2.4.2): field widths chosen
    by the FileNode header; compressed formats store value/8."""
    sfmt, smul = _STP_FMT[stp_fmt]
    cfmt, cmul = _CB_FMT[cb_fmt]
    _check(buf, pos, struct.calcsize(sfmt) + struct.calcsize(cfmt),
           "FileNodeChunkReference")
    stp = struct.unpack_from(sfmt, buf, pos)[0]
    pos += struct.calcsize(sfmt)
    cb = struct.unpack_from(cfmt, buf, pos)[0]
    pos += struct.calcsize(cfmt)
    return ChunkRef(stp * smul, cb * cmul), pos


def _resolve(buf: bytes, ref: ChunkRef, what: str) -> bytes:
    if ref.is_nil:
        raise OneStoreError(f"nil chunk reference for {what}")
    if ref.stp + ref.cb > len(buf):
        raise OneStoreError(f"{what} reference beyond end of file "
                            f"(stp={ref.stp} cb={ref.cb})")
    return buf[ref.stp:ref.stp + ref.cb]


# ------------------------------------------------------------- property sets

@dataclass
class PropValue:
    pid: int          # full 32-bit PropertyID
    value: object     # per-type python value (see parse)

    @property
    def id(self) -> int:
        return self.pid & 0x3FFFFFF

    @property
    def ptype(self) -> int:
        return (self.pid >> 26) & 0x1F


@dataclass
class PropSet:
    entries: list[PropValue] = field(default_factory=list)

    def get(self, prop_id: int) -> PropValue | None:
        """Lookup by the 26-bit property id."""
        prop_id &= 0x3FFFFFF
        for e in self.entries:
            if e.id == prop_id:
                return e
        return None

    def __contains__(self, prop_id: int) -> bool:
        return self.get(prop_id) is not None


def _parse_prop_set(buf, pos) -> tuple[PropSet, int]:
    _check(buf, pos, 2, "PropertySet header")
    count = _u16(buf, pos)
    pos += 2
    pids = []
    for _ in range(count):
        _check(buf, pos, 4, "PropertyID")
        pids.append(_u32(buf, pos))
        pos += 4
    entries = []
    for pid in pids:
        value, pos = _parse_prop_value(buf, pos, pid)
        entries.append(PropValue(pid, value))
    return PropSet(entries), pos


def _parse_prop_value(buf, pos, pid) -> tuple[object, int]:
    ptype = (pid >> 26) & 0x1F
    if ptype == PT_EMPTY:
        return None, pos
    if ptype == PT_BOOL:
        return bool(pid >> 31), pos
    if ptype == PT_U8:
        _check(buf, pos, 1, "u8 property")
        return buf[pos], pos + 1
    if ptype == PT_U16:
        _check(buf, pos, 2, "u16 property")
        return _u16(buf, pos), pos + 2
    if ptype in (PT_U32, PT_OID_ARRAY, PT_OSID_ARRAY, PT_CTXID_ARRAY):
        _check(buf, pos, 4, "u32 property")
        return _u32(buf, pos), pos + 4
    if ptype == PT_U64:
        _check(buf, pos, 8, "u64 property")
        return _u64(buf, pos), pos + 8
    if ptype == PT_DATA:
        _check(buf, pos, 4, "data property size")
        size = _u32(buf, pos)
        pos += 4
        _check(buf, pos, size, "data property payload")
        return bytes(buf[pos:pos + size]), pos + size
    if ptype in (PT_OID, PT_OSID, PT_CTXID):
        return None, pos  # reference; consumed from the id streams
    if ptype == PT_PROPERTY_VALUES:
        _check(buf, pos, 8, "property-values header")
        count = _u32(buf, pos)
        inner_pid = _u32(buf, pos + 4)
        pos += 8
        sets = []
        for _ in range(count):
            ps, pos = _parse_prop_set(buf, pos)
            sets.append(ps)
        return (inner_pid, sets), pos
    if ptype == PT_PROPERTY_SET:
        return _parse_prop_set(buf, pos)
    raise OneStoreError(f"unknown property type 0x{ptype:x} "
                        f"(PropertyID 0x{pid:08x})")


def _oid_consumption(entries: list[PropValue]) -> int:
    """CompactIds a property list consumes from the OID stream."""
    total = 0
    for e in entries:
        t = e.ptype
        if t == PT_OID:
            total += 1
        elif t == PT_OID_ARRAY:
            total += int(e.value)  # type: ignore[arg-type]
        elif t == PT_PROPERTY_VALUES:
            _pid, sets = e.value  # type: ignore[misc]
            for s in sets:
                total += _oid_consumption(s.entries)
        elif t == PT_PROPERTY_SET:
            total += _oid_consumption(e.value.entries)  # type: ignore
    return total


@dataclass
class RawPropSetBlob:
    """A parsed ObjectSpaceObjectPropSet: id streams + property set."""
    oids: list[int] = field(default_factory=list)     # CompactId u32s
    osids: list[int] = field(default_factory=list)
    ctxids: list[int] = field(default_factory=list)
    props: PropSet = field(default_factory=PropSet)


def parse_object_prop_set(data: bytes) -> RawPropSetBlob:
    """[MS-ONESTORE] 2.6.1 ObjectSpaceObjectPropSet."""
    pos = 0

    def stream(pos):
        _check(data, pos, 4, "ObjectSpaceObjectStreamHeader")
        header = _u32(data, pos)
        pos += 4
        count = header & 0xFFFFFF
        extended = bool(header & (1 << 30))
        osid_absent = bool(header & (1 << 31))
        ids = []
        for _ in range(count):
            _check(data, pos, 4, "CompactId")
            ids.append(_u32(data, pos))
            pos += 4
        return ids, extended, osid_absent, pos

    oids, extended, osid_absent, pos = stream(pos)
    osids: list[int] = []
    ctxids: list[int] = []
    if not osid_absent:
        osids, extended2, _absent, pos = stream(pos)
        if extended2:
            ctxids, _e, _a, pos = stream(pos)
    props, pos = _parse_prop_set(data, pos)
    # trailing padding allowed; parsed region must not overrun
    return RawPropSetBlob(oids, osids, ctxids, props)


# ------------------------------------------------------------------ FileNode

@dataclass
class FileNode:
    node_id: int
    ref: ChunkRef | None = None
    children: list["FileNode"] | None = None  # BaseType 2 target list
    data: dict = field(default_factory=dict)


def _exguid(buf, pos) -> tuple[ExGuid, int]:
    _check(buf, pos, 20, "ExtendedGUID")
    return (read_guid(buf, pos), _u32(buf, pos + 16)), pos + 20


def _compact_ids(raw: list[int], table: dict[int, str],
                 what: str) -> list[ExGuid]:
    out = []
    for cid in raw:
        n = cid & 0xFF
        index = cid >> 8
        guid = table.get(index)
        if guid is None:
            if cid == 0:
                out.append(("00000000-0000-0000-0000-000000000000", 0))
                continue
            raise OneStoreError(
                f"unresolvable CompactId index {index} in {what}")
        out.append((guid, n))
    return out


class _Walker:
    """FileNode framing walk over one classic revision store."""

    def __init__(self, buf: bytes, node_counts: dict[int, int]):
        self.buf = buf
        self.node_counts = dict(node_counts)

    # -- fragments ----------------------------------------------------------

    def parse_list(self, ref: ChunkRef) -> list[FileNode]:
        """Follow a FileNodeListFragment chain -> flat FileNode sequence."""
        nodes: list[FileNode] = []
        expected_seq = 0
        remaining: int | None = None
        seen = set()
        while not (ref.is_nil or ref.is_zero):
            if ref.stp in seen:
                raise OneStoreError("FileNodeListFragment chain cycle")
            seen.add(ref.stp)
            frag = _resolve(self.buf, ref, "FileNodeListFragment")
            if len(frag) < 36:
                raise OneStoreError("FileNodeListFragment too small")
            if _u64(frag, 0) != FRAGMENT_MAGIC:
                raise OneStoreError("bad FileNodeListFragment magic")
            list_id = _u32(frag, 8)
            seq = _u32(frag, 12)
            if seq != expected_seq:
                raise OneStoreError(
                    f"fragment sequence {seq}, expected {expected_seq}")
            expected_seq += 1
            if remaining is None:
                remaining = self.node_counts.get(list_id)
                if remaining is None:
                    _logger.debug("onestore: list 0x%x missing from the "
                                  "transaction log; scanning whole fragments",
                                  list_id)
            if _u64(frag, len(frag) - 8) != FRAGMENT_FOOTER:
                raise OneStoreError("bad FileNodeListFragment footer")

            pos = 16
            area_end = len(frag) - 20  # nextFragment (12) + footer (8)
            while area_end - pos >= 4 and (remaining is None or remaining > 0):
                node, pos = self.parse_node(frag, pos)
                if node.node_id == 0:  # padding
                    continue
                if node.node_id == FN_CHUNK_TERMINATOR:
                    break
                nodes.append(node)
                if remaining is not None:
                    remaining -= 1
            ref, _ = read_fcr64x32(frag, len(frag) - 20)
        return nodes

    # -- one node -----------------------------------------------------------

    def parse_node(self, frag, pos) -> tuple[FileNode, int]:
        _check(frag, pos, 4, "FileNode header")
        header = _u32(frag, pos)
        node_id = header & 0x3FF
        declared = (header >> 10) & 0x1FFF
        stp_fmt = (header >> 23) & 0x3
        cb_fmt = (header >> 25) & 0x3
        base_type = (header >> 27) & 0xF
        start = pos
        pos += 4
        if node_id == 0:
            return FileNode(0), pos

        node = FileNode(node_id)
        if base_type in (1, 2):
            node.ref, pos = read_file_node_chunk_ref(frag, pos,
                                                     stp_fmt, cb_fmt)
            if base_type == 2 and not (node.ref.is_nil or node.ref.is_zero):
                node.children = self.parse_list(node.ref)

        pos = self._parse_fnd(frag, pos, node)

        used = pos - start
        if declared and used > declared:
            raise OneStoreError(
                f"FileNode 0x{node_id:03x} overran declared size "
                f"({used} > {declared})")
        if declared and used < declared:
            # unknown trailing payload; declared size is authoritative
            pos = start + declared
        return node, pos

    def _parse_fnd(self, frag, pos, node: FileNode) -> int:
        nid = node.node_id
        d = node.data
        if nid == FN_OBJECT_SPACE_MANIFEST_ROOT:
            d["gosid"], pos = _exguid(frag, pos)
        elif nid in (FN_OBJECT_SPACE_MANIFEST_LIST_REF,
                     FN_OBJECT_SPACE_MANIFEST_LIST_START):
            d["gosid"], pos = _exguid(frag, pos)
        elif nid == FN_REVISION_MANIFEST_LIST_START:
            d["gosid"], pos = _exguid(frag, pos)
            pos += 4  # nInstance
        elif nid == FN_REVISION_MANIFEST_START4:
            d["rid"], pos = _exguid(frag, pos)
            d["rid_dependent"], pos = _exguid(frag, pos)
            pos += 8 + 4 + 2  # timeCreation, revisionRole, odcsDefault
        elif nid in (FN_REVISION_MANIFEST_START6, FN_REVISION_MANIFEST_START7):
            d["rid"], pos = _exguid(frag, pos)
            d["rid_dependent"], pos = _exguid(frag, pos)
            pos += 4 + 2
            if nid == FN_REVISION_MANIFEST_START7:
                d["gctxid"], pos = _exguid(frag, pos)
        elif nid == FN_GLOBAL_ID_TABLE_START:
            pos += 1  # reserved
        elif nid == FN_GLOBAL_ID_TABLE_ENTRY:
            d["index"] = _u32(frag, pos)
            d["guid"] = read_guid(frag, pos + 4)
            pos += 20
        elif nid == FN_GLOBAL_ID_TABLE_ENTRY2:
            d["map_from"] = _u32(frag, pos)
            d["map_to"] = _u32(frag, pos + 4)
            pos += 8
        elif nid == FN_GLOBAL_ID_TABLE_ENTRY3:
            pos += 12
        elif nid in (FN_OBJECT_DECL_WITH_REF_COUNT,
                     FN_OBJECT_DECL_WITH_REF_COUNT2):
            # body: CompactId + (jci 10 bits | odcs 4 bits) + 2 reserved
            d["oid_raw"] = _u32(frag, pos)
            jci_field = _u32(frag, pos + 4)
            d["jcid"] = (jci_field & 0x3FF) | 0x20000  # IsPropertySet
            pos += 10
            pos += 1 if nid == FN_OBJECT_DECL_WITH_REF_COUNT else 4  # cRef
            d["propset"] = parse_object_prop_set(
                _resolve(self.buf, node.ref, "object property set"))
        elif nid in (FN_OBJECT_REVISION_WITH_REF_COUNT,
                     FN_OBJECT_REVISION_WITH_REF_COUNT2):
            d["oid_raw"] = _u32(frag, pos)
            pos += 4
            pos += 1 if nid == FN_OBJECT_REVISION_WITH_REF_COUNT else 8
            d["propset"] = parse_object_prop_set(
                _resolve(self.buf, node.ref, "object revision property set"))
        elif nid == FN_ROOT_OBJECT_REFERENCE2:
            d["oid_raw"] = _u32(frag, pos)
            d["role"] = _u32(frag, pos + 4)
            pos += 8
        elif nid == FN_ROOT_OBJECT_REFERENCE3:
            d["oid"], pos = _exguid(frag, pos)
            d["role"] = _u32(frag, pos)
            pos += 4
        elif nid == FN_REVISION_ROLE_DECLARATION:
            d["rid"], pos = _exguid(frag, pos)
            pos += 4
        elif nid == FN_REVISION_ROLE_AND_CONTEXT:
            d["rid"], pos = _exguid(frag, pos)
            pos += 4
            d["gctxid"], pos = _exguid(frag, pos)
        elif nid in (FN_OBJECT_DECL_FILE_DATA3,
                     FN_OBJECT_DECL_FILE_DATA3_LARGE):
            d["oid_raw"] = _u32(frag, pos)
            d["jcid"] = _u32(frag, pos + 4)
            pos += 8
            pos += 1 if nid == FN_OBJECT_DECL_FILE_DATA3 else 4
            for key in ("file_data_ref", "file_ext"):
                cch = _u32(frag, pos)
                pos += 4
                _check(frag, pos, cch * 2, "StringInStorageBuffer")
                d[key] = bytes(frag[pos:pos + cch * 2]).decode(
                    "utf-16-le", errors="replace")
                pos += cch * 2
        elif nid == FN_OBJECT_INFO_DEPENDENCY_OVERRIDES:
            if node.ref is not None and node.ref.is_nil:
                c8 = _u32(frag, pos)
                c32 = _u32(frag, pos + 4)
                pos += 12 + c8 * 5 + c32 * 8
        elif nid == FN_DATA_SIGNATURE_GROUP:
            d["guid"], pos = _exguid(frag, pos)
        elif nid == FN_FILE_DATA_STORE_OBJECT_REF:
            d["guid"] = read_guid(frag, pos)
            pos += 16
        elif nid in (FN_OBJECT_DECL2_REF_COUNT,
                     FN_OBJECT_DECL2_LARGE_REF_COUNT,
                     FN_READONLY_OBJECT_DECL2_REF_COUNT,
                     FN_READONLY_OBJECT_DECL2_LARGE):
            d["oid_raw"] = _u32(frag, pos)
            d["jcid"] = _u32(frag, pos + 4)
            pos += 9  # CompactId + JCID + flags byte
            pos += 1 if nid in (FN_OBJECT_DECL2_REF_COUNT,
                                FN_READONLY_OBJECT_DECL2_REF_COUNT) else 4
            if nid in (FN_READONLY_OBJECT_DECL2_REF_COUNT,
                       FN_READONLY_OBJECT_DECL2_LARGE):
                pos += 16  # md5 hash
            d["propset"] = parse_object_prop_set(
                _resolve(self.buf, node.ref, "object property set"))
        elif nid == FN_OBJECT_GROUP_LIST_REF:
            d["group_id"], pos = _exguid(frag, pos)
        elif nid == FN_OBJECT_GROUP_START:
            d["group_oid"], pos = _exguid(frag, pos)
        elif nid == FN_HASHED_CHUNK_DESCRIPTOR2:
            pos += 16  # hash
        elif nid in (FN_REVISION_MANIFEST_END, FN_GLOBAL_ID_TABLE_START2,
                     FN_GLOBAL_ID_TABLE_END, FN_OBJECT_GROUP_END,
                     FN_OBJECT_DATA_ENCRYPTION_KEY,
                     FN_REVISION_MANIFEST_LIST_REF,
                     FN_FILE_DATA_STORE_LIST_REF, FN_CHUNK_TERMINATOR):
            pass
        else:
            _logger.debug("onestore: unknown FileNodeID 0x%03x", nid)
        return pos


# ----------------------------------------------------------- object assembly

@dataclass
class OneObject:
    jcid: int
    props: PropSet
    oids: list[ExGuid] = field(default_factory=list)
    osids: list[ExGuid] = field(default_factory=list)
    ctxids: list[ExGuid] = field(default_factory=list)
    file_data_ref: str | None = None
    file_ext: str | None = None

    def ref_at(self, prop_id: int) -> ExGuid | None:
        """Resolve a PT_OID property to its ExGuid (positional stream)."""
        e = self.props.get(prop_id)
        if e is None or e.ptype != PT_OID:
            return None
        index = self._offset_of(e)
        return self.oids[index] if index < len(self.oids) else None

    def ref_array_at(self, prop_id: int) -> list[ExGuid] | None:
        e = self.props.get(prop_id)
        if e is None or e.ptype != PT_OID_ARRAY:
            return None
        index = self._offset_of(e)
        count = int(e.value)  # type: ignore[arg-type]
        return self.oids[index:index + count]

    def _offset_of(self, entry: PropValue) -> int:
        idx = self.props.entries.index(entry)
        return _oid_consumption(self.props.entries[:idx])


@dataclass
class ObjectSpace:
    gosid: ExGuid
    roots: dict[int, ExGuid] = field(default_factory=dict)
    objects: dict[ExGuid, OneObject] = field(default_factory=dict)


@dataclass
class OneStore:
    file_type: str
    object_spaces: list[ObjectSpace] = field(default_factory=list)
    root_gosid: ExGuid | None = None

    @property
    def root_space(self) -> ObjectSpace | None:
        for s in self.object_spaces:
            if s.gosid == self.root_gosid:
                return s
        return None


def _transaction_counts(buf: bytes, ref: ChunkRef,
                        committed: int) -> dict[int, int]:
    """Replay the transaction log: last committed count per FileNodeList."""
    counts: dict[int, int] = {}
    transactions_seen = 0
    pending: dict[int, int] = {}
    seen = set()
    while not (ref.is_nil or ref.is_zero) and transactions_seen < committed:
        if ref.stp in seen:
            raise OneStoreError("transaction log fragment cycle")
        seen.add(ref.stp)
        frag = _resolve(buf, ref, "TransactionLogFragment")
        n_entries = (len(frag) - 12) // 8
        pos = 0
        for _ in range(n_entries):
            src_id = _u32(frag, pos)
            switch = _u32(frag, pos + 4)
            pos += 8
            if src_id == 0x00000001:  # sentinel: transaction commit marker
                counts.update(pending)
                pending.clear()
                transactions_seen += 1
                if transactions_seen >= committed:
                    break
            elif src_id != 0:
                pending[src_id] = switch
        ref, _ = read_fcr64x32(frag, len(frag) - 12)
    return counts


def _replay_objects(nodes: list[FileNode], space: ObjectSpace) -> None:
    """Replay one revision-manifest list into the space's object map."""
    id_table: dict[int, str] = {}
    current_table: dict[int, str] = {}
    in_table = False

    def add_object(node: FileNode, table: dict[int, str]) -> None:
        d = node.data
        blob: RawPropSetBlob = d.get("propset") or RawPropSetBlob()
        obj = OneObject(
            jcid=d.get("jcid", 0),
            props=blob.props,
            oids=_compact_ids(blob.oids, table, "OID stream"),
            osids=_compact_ids(blob.osids, table, "OSID stream"),
            ctxids=_compact_ids(blob.ctxids, table, "ContextID stream"),
            file_data_ref=d.get("file_data_ref"),
            file_ext=d.get("file_ext"),
        )
        oid = _compact_ids([d["oid_raw"]], table, "object id")[0]
        space.objects[oid] = obj

    def walk(nodes: list[FileNode], table: dict[int, str]) -> None:
        nonlocal id_table, current_table, in_table
        for node in nodes:
            nid = node.node_id
            if nid in (FN_GLOBAL_ID_TABLE_START, FN_GLOBAL_ID_TABLE_START2):
                current_table = {}
                in_table = True
            elif nid == FN_GLOBAL_ID_TABLE_ENTRY and in_table:
                current_table[node.data["index"]] = node.data["guid"]
            elif nid == FN_GLOBAL_ID_TABLE_ENTRY2 and in_table:
                _logger.debug("onestore: GlobalIdTableEntry2FNDX (parent "
                              "table remap) ignored")
            elif nid == FN_GLOBAL_ID_TABLE_END:
                in_table = False
                id_table = current_table
                table.clear()
                table.update(current_table)
            elif nid == FN_OBJECT_GROUP_LIST_REF:
                group_table: dict[int, str] = {}
                walk(node.children or [], group_table)
            elif nid in (FN_OBJECT_DECL2_REF_COUNT,
                         FN_OBJECT_DECL2_LARGE_REF_COUNT,
                         FN_READONLY_OBJECT_DECL2_REF_COUNT,
                         FN_READONLY_OBJECT_DECL2_LARGE,
                         FN_OBJECT_DECL_WITH_REF_COUNT,
                         FN_OBJECT_DECL_WITH_REF_COUNT2,
                         FN_OBJECT_DECL_FILE_DATA3,
                         FN_OBJECT_DECL_FILE_DATA3_LARGE):
                add_object(node, table if table else id_table)
            elif nid in (FN_OBJECT_REVISION_WITH_REF_COUNT,
                         FN_OBJECT_REVISION_WITH_REF_COUNT2):
                add_object(node, table if table else id_table)
            elif nid == FN_ROOT_OBJECT_REFERENCE3:
                # first declaration wins: the current revision comes first
                # in the list [inferred: onenote.rs or_insert behavior]
                space.roots.setdefault(node.data["role"], node.data["oid"])
            elif nid == FN_ROOT_OBJECT_REFERENCE2:
                resolved = _compact_ids([node.data["oid_raw"]],
                                        table if table else id_table,
                                        "root object")[0]
                space.roots.setdefault(node.data["role"], resolved)
            # revision start/end, role declarations, signatures: framing only

    walk(nodes, {})


def parse_onestore(data: bytes) -> OneStore:
    """Parse a classic OneNote 2016 revision store into object spaces."""
    if len(data) < 1024:
        raise OneStoreError("file too small for a OneStore header")
    file_type = read_guid(data, 0)
    if file_type not in (GUID_FILE_TYPE_ONE, GUID_FILE_TYPE_TOC2):
        raise OneStoreError("not a OneNote revision store (bad guidFileType)")
    file_format = read_guid(data, 48)
    if file_format == GUID_FILE_FORMAT_FSSHTTPB:
        raise OneStoreError(
            "FSSHTTPB-packaged OneNote file (cloud format) is not supported "
            "yet; only the classic OneNote 2016 revision store is")
    if file_format != GUID_FILE_FORMAT_CLASSIC:
        raise OneStoreError(f"unknown guidFileFormat {file_format}")

    c_transactions = _u32(data, 96)
    fcr_transaction_log, _ = read_fcr64x32(data, 160)
    fcr_root_list, _ = read_fcr64x32(data, 172)

    counts = _transaction_counts(data, fcr_transaction_log, c_transactions)
    walker = _Walker(data, counts)

    if fcr_root_list.is_nil or fcr_root_list.is_zero:
        return OneStore(file_type)
    root_nodes = walker.parse_list(fcr_root_list)

    store = OneStore(file_type)
    for node in root_nodes:
        if node.node_id == FN_OBJECT_SPACE_MANIFEST_ROOT:
            store.root_gosid = node.data["gosid"]
        elif node.node_id == FN_OBJECT_SPACE_MANIFEST_LIST_REF:
            space = ObjectSpace(gosid=node.data["gosid"])
            manifest_nodes = node.children or []
            # last revision-manifest-list reference wins ([MS-ONESTORE] 2.1.6)
            rev_lists = [n for n in manifest_nodes
                         if n.node_id == FN_REVISION_MANIFEST_LIST_REF]
            if rev_lists:
                _replay_objects(rev_lists[-1].children or [], space)
            store.object_spaces.append(space)
        # FileDataStoreListReference / others: not needed for ink
    return store
