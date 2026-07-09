#!/usr/bin/env python3
"""Schema-less FlatBuffers walker (a `flatc --annotate`-style explorer).

Decodes FlatBuffers *framing* without a schema: root table via its vtable,
recursing into anything that validates as a table / string / vector-of-
offsets, and dumping everything else as raw slot bytes with scalar
interpretations. Used to explore undocumented FlatBuffers-based note
formats (Notability .ntb / cloud blobs). Pure stdlib.

Binary format facts (all from the published FlatBuffers internals doc,
https://flatbuffers.dev/internals/ — no third-party code):

  file      = u32 root_uoffset [4-byte file_id] ...
  table     = i32 soffset_to_vtable (vtable = table_pos - soffset), fields
  vtable    = u16 vtable_bytes, u16 table_bytes, u16 slot_off[*]
              (slot_off is relative to table start; 0 = field absent)
  string    = u32 length, bytes, NUL
  vector    = u32 count, elements (element type/size needs a schema)
  uoffset   = u32, relative to its own position, always forward
  scalars/structs are stored inline in the table, aligned to their size

Without a schema, field types are guessed:
  - a 4-byte slot whose u32, read as a uoffset, lands on a valid string /
    table / vector is reported as such (validation is strict enough that
    false positives are rare but possible — treat output as evidence,
    not ground truth);
  - vectors are probed as vector-of-table-offsets / vector-of-string-
    offsets; anything else is dumped raw (element size unknown);
  - remaining slots are dumped as bytes with u8..u64/f32/f64 readings.
    Slot size = gap to the next-higher slot offset (or end of table).

Usage:
  fbwalk.py FILE                 # walk from the root table
  fbwalk.py FILE --table 0x1234  # walk a table at a known absolute offset
  fbwalk.py FILE --max-depth 8 --max-vec 4
"""
from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass, field

MAX_VTABLE_BYTES = 2048   # sanity bound; real vtables are small
MAX_TABLE_BYTES = 65536


class FbError(ValueError):
    pass


def u16(buf: bytes, pos: int) -> int:
    return struct.unpack_from("<H", buf, pos)[0]


def u32(buf: bytes, pos: int) -> int:
    return struct.unpack_from("<I", buf, pos)[0]


def i32(buf: bytes, pos: int) -> int:
    return struct.unpack_from("<i", buf, pos)[0]


# --- validation -----------------------------------------------------------

def vtable_of(buf: bytes, tpos: int) -> tuple[int, int, list[int]] | None:
    """If a valid table starts at tpos, return (vtable_bytes, table_bytes,
    slot_offsets); else None."""
    if tpos < 0 or tpos + 4 > len(buf):
        return None
    vpos = tpos - i32(buf, tpos)
    if vpos < 0 or vpos + 4 > len(buf):
        return None
    vbytes = u16(buf, vpos)
    tbytes = u16(buf, vpos + 2)
    if (vbytes < 4 or vbytes % 2 or vbytes > MAX_VTABLE_BYTES
            or vpos + vbytes > len(buf)):
        return None
    if tbytes < 4 or tbytes > MAX_TABLE_BYTES or tpos + tbytes > len(buf):
        return None
    slots = [u16(buf, vpos + 4 + 2 * i) for i in range((vbytes - 4) // 2)]
    # every present field must live inside the table, past the soffset
    if any(s and not (4 <= s < tbytes) for s in slots):
        return None
    return vbytes, tbytes, slots


def string_at(buf: bytes, pos: int) -> str | None:
    """If a valid NUL-terminated, mostly-printable string starts at pos."""
    if pos < 0 or pos + 4 > len(buf):
        return None
    n = u32(buf, pos)
    end = pos + 4 + n
    if end >= len(buf) or buf[end] != 0:
        return None
    raw = buf[pos + 4:end]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if raw and sum(32 <= b < 127 or b in (9, 10, 13) for b in raw) < 0.9 * n:
        return None
    return text


# --- nodes ----------------------------------------------------------------

@dataclass
class Node:
    kind: str          # table | string | vector | raw | scalar | error
    pos: int           # absolute offset of the object / slot
    size: int = 0
    label: str = ""
    children: list[tuple[str, "Node"]] = field(default_factory=list)


def walk_table(buf: bytes, tpos: int, depth: int, max_depth: int,
               max_vec: int, seen: set[int]) -> Node:
    vt = vtable_of(buf, tpos)
    if vt is None:
        return Node("error", tpos, label="not a valid table")
    vbytes, tbytes, slots = vt
    node = Node("table", tpos, tbytes,
                label=f"table vtable={len(slots)} slots, {tbytes}B")
    if tpos in seen:
        node.label += " (already visited)"
        return node
    seen.add(tpos)
    if depth >= max_depth:
        node.label += " (max depth)"
        return node

    present = sorted((off, i) for i, off in enumerate(slots) if off)
    for k, (off, idx) in enumerate(present):
        nxt = present[k + 1][0] if k + 1 < len(present) else tbytes
        gap = nxt - off
        pos = tpos + off
        node.children.append(
            (f"field_{idx}", walk_slot(buf, pos, gap, depth, max_depth,
                                       max_vec, seen)))
    return node


def walk_slot(buf: bytes, pos: int, gap: int, depth: int, max_depth: int,
              max_vec: int, seen: set[int]) -> Node:
    """A table slot: try offset-to-something, fall back to scalar bytes."""
    if gap >= 4:
        target = pos + u32(buf, pos)
        if target > pos and target + 4 <= len(buf):
            s = string_at(buf, target)
            if s is not None:
                return Node("string", target, len(s),
                            label=f"string({len(s)}) {s!r:.80}")
            if vtable_of(buf, target) is not None:
                return walk_table(buf, target, depth + 1, max_depth,
                                  max_vec, seen)
            v = walk_vector(buf, target, depth, max_depth, max_vec, seen)
            if v is not None:
                return v
    return scalar_node(buf, pos, gap)


def walk_vector(buf: bytes, vpos: int, depth: int, max_depth: int,
                max_vec: int, seen: set[int]) -> Node | None:
    if vpos + 4 > len(buf):
        return None
    count = u32(buf, vpos)
    data = vpos + 4
    if count == 0:
        return Node("vector", vpos, 4, label="vector[0]")
    # probe: vector of uoffsets to tables / strings
    probe = min(count, 16)
    if data + 4 * count <= len(buf):
        elems = [data + 4 * i for i in range(probe)]
        targets = [e + u32(buf, e) for e in elems]
        if all(t > e and vtable_of(buf, t) is not None
               for e, t in zip(elems, targets)):
            node = Node("vector", vpos, 4 + 4 * count,
                        label=f"vector[{count}] of tables")
            if depth < max_depth:
                for i in range(min(count, max_vec)):
                    e = data + 4 * i
                    node.children.append(
                        (f"[{i}]", walk_table(buf, e + u32(buf, e),
                                              depth + 1, max_depth,
                                              max_vec, seen)))
                if count > max_vec:
                    node.children.append(
                        (f"[{max_vec}..{count - 1}]",
                         Node("raw", data + 4 * max_vec, label="…elided…")))
            return node
        if all(t > e and string_at(buf, t) is not None
               for e, t in zip(elems, targets)):
            node = Node("vector", vpos, 4 + 4 * count,
                        label=f"vector[{count}] of strings")
            for i in range(min(count, max_vec)):
                e = data + 4 * i
                t = e + u32(buf, e)
                node.children.append((f"[{i}]", Node(
                    "string", t, label=f"{string_at(buf, t)!r:.80}")))
            return node
    # raw vector: element size unknown. Only accept if a plausible payload
    # fits (u8 elements at minimum), else this u32 wasn't a vector at all.
    if count > len(buf) - data:
        return None
    return Node("raw", vpos, 4 + count,
                label=f"vector-ish[count={count}] raw @+4: "
                      f"{preview(buf[data:data + 64])}")


def scalar_node(buf: bytes, pos: int, gap: int) -> Node:
    raw = buf[pos:pos + gap]
    readings = []
    if gap >= 1:
        readings.append(f"u8={raw[0]}")
    if gap >= 2:
        readings.append(f"u16={u16(buf, pos)}")
    if gap >= 4:
        readings.append(f"u32={u32(buf, pos)}")
        readings.append(f"f32={struct.unpack_from('<f', buf, pos)[0]:.6g}")
    if gap >= 8:
        readings.append(f"u64={struct.unpack_from('<Q', buf, pos)[0]}")
        readings.append(f"f64={struct.unpack_from('<d', buf, pos)[0]:.6g}")
    return Node("scalar", pos, gap,
                label=f"{gap}B {preview(raw)}  [{', '.join(readings)}]")


def preview(data: bytes, limit: int = 32) -> str:
    hexs = data[:limit].hex()
    return f"0x{hexs}" + ("…" if len(data) > limit else "")


def render(node: Node, name: str = "root", indent: int = 0) -> str:
    pad = "  " * indent
    lines = [f"{pad}{name} @0x{node.pos:x}: {node.label}"]
    for child_name, child in node.children:
        lines.append(render(child, child_name, indent + 1))
    return "\n".join(lines)


def walk_file(buf: bytes, table_at: int | None = None, max_depth: int = 12,
              max_vec: int = 8) -> Node:
    tpos = u32(buf, 0) if table_at is None else table_at
    return walk_table(buf, tpos, 0, max_depth, max_vec, set())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file")
    ap.add_argument("--table", type=lambda s: int(s, 0), default=None,
                    help="absolute offset of a table to walk (default: root)")
    ap.add_argument("--max-depth", type=int, default=12)
    ap.add_argument("--max-vec", type=int, default=8,
                    help="max vector elements to expand")
    args = ap.parse_args()
    buf = open(args.file, "rb").read()
    print(render(walk_file(buf, args.table, args.max_depth, args.max_vec)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
