"""Low-level decoders for the GoodNotes container.

Protobuf wire walking + Apple-framed LZ4 (independent implementations from
the published format specs; see tools/re/pbwire.py and tools/re/applelz4.py
for the exploratory versions these were distilled from).

Confidence per field: docs/formats/goodnotes.md.
"""
from __future__ import annotations

import struct
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
    value: object


def parse_message(buf: bytes) -> list[Field]:
    fields, pos = [], 0
    while pos < len(buf):
        key, pos = read_varint(buf, pos)
        number, wtype = key >> 3, key & 7
        if number == 0:
            raise WireError("field number 0")
        if wtype == 0:
            value, pos = read_varint(buf, pos)
        elif wtype == 1:
            if pos + 8 > len(buf):
                raise WireError("truncated fixed64")
            value = struct.unpack_from("<d", buf, pos)[0]
            pos += 8
        elif wtype == 2:
            length, pos = read_varint(buf, pos)
            if pos + length > len(buf):
                raise WireError("truncated bytes field")
            value = buf[pos:pos + length]
            pos += length
        elif wtype == 5:
            if pos + 4 > len(buf):
                raise WireError("truncated fixed32")
            value = struct.unpack_from("<f", buf, pos)[0]
            pos += 4
        else:
            raise WireError(f"unsupported wire type {wtype}")
        fields.append(Field(number, wtype, value))
    return fields


def fields_by_number(buf: bytes) -> dict[int, list[Field]]:
    out: dict[int, list[Field]] = {}
    for f in parse_message(buf):
        out.setdefault(f.number, []).append(f)
    return out


def split_delimited(buf: bytes) -> list[bytes]:
    """<varint len><message> stream -> raw message blobs."""
    out, pos = [], 0
    while pos < len(buf):
        length, pos = read_varint(buf, pos)
        if pos + length > len(buf):
            raise WireError(f"record at {pos} overruns buffer")
        out.append(buf[pos:pos + length])
        pos += length
    return out


# --- Apple libcompression framed LZ4 ---------------------------------------

def lz4_block_decompress(src: bytes, expected_size: int) -> bytes:
    out = bytearray()
    pos = 0
    n = len(src)
    while pos < n:
        token = src[pos]
        pos += 1
        lit_len = token >> 4
        if lit_len == 15:
            while True:
                b = src[pos]
                pos += 1
                lit_len += b
                if b != 255:
                    break
        out += src[pos:pos + lit_len]
        pos += lit_len
        if pos >= n:
            break
        offset = struct.unpack_from("<H", src, pos)[0]
        pos += 2
        if offset == 0:
            raise WireError("LZ4 match offset 0")
        match_len = (token & 0x0F) + 4
        if (token & 0x0F) == 15:
            while True:
                b = src[pos]
                pos += 1
                match_len += b
                if b != 255:
                    break
        start = len(out) - offset
        if start < 0:
            raise WireError("LZ4 match before start of output")
        for i in range(match_len):  # byte-wise: matches may overlap
            out.append(out[start + i])
    if len(out) != expected_size:
        raise WireError(f"LZ4: got {len(out)}B, expected {expected_size}B")
    return bytes(out)


def apple_lz4_decompress(buf: bytes) -> bytes:
    """bv41/bv4-/bv4$ framed stream -> plain bytes."""
    out = bytearray()
    pos = 0
    while pos < len(buf):
        magic = buf[pos:pos + 4]
        if magic == b"bv4$":
            break
        if magic == b"bv41":
            dec_size, comp_size = struct.unpack_from("<II", buf, pos + 4)
            out += lz4_block_decompress(buf[pos + 12:pos + 12 + comp_size],
                                        dec_size)
            pos += 12 + comp_size
        elif magic == b"bv4-":
            size = struct.unpack_from("<I", buf, pos + 4)[0]
            out += buf[pos + 8:pos + 8 + size]
            pos += 8 + size
        else:
            raise WireError(f"unknown LZ4 block magic {magic!r} at {pos}")
    return bytes(out)
