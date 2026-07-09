#!/usr/bin/env python3
"""Minimal protobuf wire-format walker (a `protoc --decode_raw` equivalent).

Schema-less: decodes tag/wire-type framing only, guessing at sub-messages.
Used to explore undocumented protobuf-based note formats. Pure stdlib.

Wire format reference: https://protobuf.dev/programming-guides/encoding/
(format facts; no third-party code).

Usage:
  pbwire.py FILE               # decode one message
  pbwire.py --delimited FILE   # <varint len><message> stream (GoodNotes pages)
"""
from __future__ import annotations

import struct
import sys
from dataclasses import dataclass


class WireError(ValueError):
    pass


def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(buf):
            raise WireError("varint past end of buffer")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise WireError("varint too long")


@dataclass
class Field:
    number: int
    wire_type: int
    value: object  # int | bytes | float | list[Field]


def parse_message(buf: bytes, max_depth: int = 12) -> list[Field]:
    """Parse a complete message; raises WireError if buf isn't one."""
    fields, pos = [], 0
    while pos < len(buf):
        key, pos = read_varint(buf, pos)
        number, wtype = key >> 3, key & 7
        if number == 0:
            raise WireError("field number 0")
        if wtype == 0:  # varint
            value, pos = read_varint(buf, pos)
        elif wtype == 1:  # 64-bit
            if pos + 8 > len(buf):
                raise WireError("truncated fixed64")
            value = struct.unpack_from("<d", buf, pos)[0]
            pos += 8
        elif wtype == 2:  # length-delimited
            length, pos = read_varint(buf, pos)
            if pos + length > len(buf):
                raise WireError("truncated bytes field")
            value = buf[pos:pos + length]
            pos += length
        elif wtype == 5:  # 32-bit
            if pos + 4 > len(buf):
                raise WireError("truncated fixed32")
            value = struct.unpack_from("<f", buf, pos)[0]
            pos += 4
        else:  # groups (3/4) unsupported; treat as malformed
            raise WireError(f"unsupported wire type {wtype}")
        fields.append(Field(number, wtype, value))
    return fields


def try_submessage(data: bytes, max_depth: int) -> list[Field] | None:
    """Heuristic: bytes that parse cleanly as a message probably are one."""
    if max_depth <= 0 or not data:
        return None
    try:
        return parse_message(data)
    except WireError:
        return None


def split_delimited(buf: bytes) -> list[bytes]:
    """Split a `<varint len><message>` stream into raw message blobs."""
    out, pos = [], 0
    while pos < len(buf):
        length, pos = read_varint(buf, pos)
        if pos + length > len(buf):
            raise WireError(f"record at {pos} overruns buffer")
        out.append(buf[pos:pos + length])
        pos += length
    return out


def _preview(data: bytes, limit: int = 48) -> str:
    printable = all(32 <= b < 127 for b in data[:limit]) and data
    if printable:
        text = data[:limit].decode("ascii")
        return f'"{text}"' + ("…" if len(data) > limit else "")
    hexs = data[:limit].hex()
    return f"0x{hexs}" + ("…" if len(data) > limit else "")


def render(fields: list[Field], indent: int = 0, max_depth: int = 12) -> str:
    pad = "  " * indent
    lines = []
    for f in fields:
        if f.wire_type == 2:
            sub = try_submessage(f.value, max_depth - 1)
            if sub is not None and len(f.value) > 1:
                lines.append(f"{pad}{f.number} {{  # {len(f.value)}B")
                lines.append(render(sub, indent + 1, max_depth - 1))
                lines.append(f"{pad}}}")
            else:
                lines.append(f"{pad}{f.number}: bytes({len(f.value)}) "
                             f"{_preview(f.value)}")
        elif f.wire_type == 5:
            lines.append(f"{pad}{f.number}: float {f.value!r}")
        elif f.wire_type == 1:
            lines.append(f"{pad}{f.number}: double {f.value!r}")
        else:
            lines.append(f"{pad}{f.number}: {f.value}")
    return "\n".join(lines)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    delimited = "--delimited" in sys.argv
    if not args:
        print(__doc__)
        return 2
    buf = open(args[0], "rb").read()
    blobs = split_delimited(buf) if delimited else [buf]
    for i, blob in enumerate(blobs):
        if delimited:
            print(f"=== record {i} ({len(blob)}B) ===")
        print(render(parse_message(blob)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
