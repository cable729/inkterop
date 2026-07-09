"""Minimal FlatBuffers builder — the inverse of ntb.py's ``_Table``.

Hand-rolled from the published FlatBuffers internals doc
(https://flatbuffers.dev/internals/), same provenance as
``tools/re/fbwalk.py``; no schema, no third-party code. Supports exactly
what a Notability noteBundle needs: explicitly-typed inline scalars,
inline structs (raw bytes + alignment), strings, byte vectors, vectors
of table offsets, and nested tables. No vtable dedup (bundles are small
and no reader cares).

Layout model: FlatBuffers files are built bottom-up, so this builder
*prepends* every object and hands around offsets measured from the END
of the growing buffer (an object's end-offset never moves as the front
grows). A uoffset field always points forward in the file, so its u32
value is simply ``field_end_offset - target_end_offset``. Absolute
alignment holds because every object start is aligned in end-offset
space and ``finish()`` pads the total size to the largest alignment
seen (abs_pos = total - end_offset).
"""
from __future__ import annotations

import struct

_SCALARS = {
    "u8": ("<B", 1),
    "u16": ("<H", 2),
    "u32": ("<I", 4),
    "u64": ("<Q", 8),
    "f32": ("<f", 4),
}

#: Table slot values accepted by :meth:`FbBuilder.table`:
#:   ("u8"|"u16"|"u32"|"u64"|"f32", value)   inline scalar
#:   ("f32s", (a, b, ...))                   inline struct of f32s
#:   ("struct", raw_bytes, align)            inline struct, verbatim
#:   ("ref", end_offset)                     uoffset to a built object


class FbBuilder:
    """One-shot builder: build leaves first, then ``finish(root_table)``."""

    def __init__(self) -> None:
        self._data = bytearray()
        self._max_align = 4  # the root uoffset itself
        self._vtables: dict[bytes, int] = {}  # dedup cache (official builders dedup)

    # --- primitive: prepend bytes with an aligned start ------------------

    def _prepend(self, raw: bytes, align: int = 1) -> int:
        """Prepend ``raw`` so its start end-offset is a multiple of
        ``align``; the pad lands between it and older (later-in-file)
        data. Returns the start end-offset."""
        self._max_align = max(self._max_align, align)
        pad = -(len(self._data) + len(raw)) % align
        self._data[:0] = raw + b"\x00" * pad
        return len(self._data)

    # --- heap objects (return end-offsets for use in "ref" slots) --------

    def string(self, s: str) -> int:
        raw = s.encode("utf-8")
        return self._prepend(struct.pack("<I", len(raw)) + raw + b"\x00", 4)

    def byte_vector(self, data: bytes) -> int:
        return self._prepend(struct.pack("<I", len(data)) + bytes(data), 4)

    def vector_of_tables(self, offsets: list[int]) -> int:
        """Vector of uoffsets to already-built tables."""
        size = 4 + 4 * len(offsets)
        start = len(self._data) + (-(len(self._data) + size) % 4) + size
        raw = bytearray(struct.pack("<I", len(offsets)))
        for k, target in enumerate(offsets):
            elem = start - 4 - 4 * k  # end-offset of element k
            raw += struct.pack("<I", elem - target)
        got = self._prepend(bytes(raw), 4)
        assert got == start
        return got

    def table(self, slots: dict[int, tuple]) -> int:
        """Build a table from {vtable_slot_index: value-tuple}; every
        given slot is emitted explicitly (no default elision — we mirror
        observed files, not a schema)."""
        offs: dict[int, int] = {}
        ends: list[int] = []  # end-offset just past each field's bytes
        # Ascending index order: field_0 is prepended first and therefore
        # lands at the HIGHEST offset inside the table — the layout the
        # official (Swift/C++) builders produce and the one observed in
        # app-made noteBundles.
        for idx in sorted(slots):
            item = slots[idx]
            kind = item[0]
            if kind == "ref":
                align = 4
                pad = -(len(self._data) + 4) % 4
                off = len(self._data) + pad + 4
                raw = struct.pack("<I", off - item[1])
            elif kind == "struct":
                raw, align = bytes(item[1]), item[2]
            elif kind == "f32s":
                raw, align = struct.pack(f"<{len(item[1])}f", *item[1]), 4
            else:
                fmt, align = _SCALARS[kind]
                raw = struct.pack(fmt, item[1])
            offs[idx] = self._prepend(raw, align)
            ends.append(offs[idx] - len(raw))

        tpos = self._prepend(b"\x00\x00\x00\x00", 4)  # soffset, patched below
        nslots = (max(slots) + 1) if slots else 0
        tbytes = (tpos - min(ends)) if ends else 4
        vt = bytearray(struct.pack("<HH", 4 + 2 * nslots, tbytes))
        for i in range(nslots):
            vt += struct.pack("<H", tpos - offs[i] if i in offs else 0)
        vt_bytes = bytes(vt)
        vpos = self._vtables.get(vt_bytes)
        if vpos is None:
            vpos = self._prepend(vt_bytes, 2)
            self._vtables[vt_bytes] = vpos
        # soffset: vtable_abs = table_abs - soffset  =>  soffset = vpos - tpos
        struct.pack_into("<i", self._data, len(self._data) - tpos, vpos - tpos)
        return tpos

    # --- root -------------------------------------------------------------

    def finish(self, root: int) -> bytes:
        """Prepend the root uoffset; pad so every alignment holds
        absolutely. The builder must not be reused afterwards."""
        pad = -(len(self._data) + 4) % self._max_align
        total = len(self._data) + pad + 4
        self._data[:0] = struct.pack("<I", total - root) + b"\x00" * pad
        return bytes(self._data)
