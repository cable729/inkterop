"""Low-level decoders + encoders for the GoodNotes container.

Protobuf wire walking + Apple-framed LZ4 (independent implementations from
the published format specs; see tools/re/pbwire.py and tools/re/applelz4.py
for the exploratory versions these were distilled from). The encoders at
the bottom are exact inverses of the decoders, used by the writer.

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


# --- encoders (exact inverses of the decoders above) -------------------------

def write_varint(value: int) -> bytes:
    if value < 0:
        raise WireError("varint must be non-negative")
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def write_tag(number: int, wire_type: int) -> bytes:
    if number < 1:
        raise WireError("field number must be >= 1")
    return write_varint((number << 3) | wire_type)


def write_varint_field(number: int, value: int) -> bytes:
    return write_tag(number, 0) + write_varint(value)


def write_len_delimited(number: int, payload: bytes) -> bytes:
    return write_tag(number, 2) + write_varint(len(payload)) + payload


def write_float32(number: int, value: float) -> bytes:
    return write_tag(number, 5) + struct.pack("<f", value)


def join_delimited(records: list[bytes]) -> bytes:
    """Raw message blobs -> <varint len><message> stream (inverse of
    split_delimited)."""
    return b"".join(write_varint(len(r)) + r for r in records)


# Apple libcompression frames blocks at 64 KiB; stay within that for the
# raw blocks we emit so real decoders never see an oversized frame.
_RAW_BLOCK_MAX = 1 << 16


def apple_lz4_compress(data: bytes) -> bytes:
    """Plain bytes -> `bv4-` raw-block framed stream + `bv4$` terminator.

    Deliberately zero compression logic: raw (stored) blocks are legal in
    the Apple frame format and round-trip through apple_lz4_decompress
    byte-identically. The container ZIP deflates on top anyway.
    """
    out = bytearray()
    pos = 0
    while pos < len(data):
        chunk = data[pos:pos + _RAW_BLOCK_MAX]
        out += b"bv4-" + struct.pack("<I", len(chunk)) + chunk
        pos += len(chunk)
    out += b"bv4$"
    return bytes(out)


_TPL_SCALAR_FMT = {"v": "<H", "u": "<f", "f": "<f"}  # 'f' size [unknown]


def encode_tpl(sections: list[tuple[str, str, list]]) -> bytes:
    """Typed sections (reader.parse_tpl's output shape) -> geometry blob.

    The type signature is derived from the sections, so
    parse_tpl(encode_tpl(sections)) == sections with zero residue.
    """
    sig_parts: list[str] = []
    body = bytearray()
    for kind, spec, vals in sections:
        if kind == "scalar":
            if spec not in _TPL_SCALAR_FMT:
                raise WireError(f"unknown tpl scalar spec {spec!r}")
            sig_parts.append(spec)
            body += struct.pack(_TPL_SCALAR_FMT[spec], vals[0])
        elif kind == "array":
            if spec not in _TPL_SCALAR_FMT:
                raise WireError(f"unknown tpl array spec {spec!r}")
            sig_parts.append(f"A({spec})")
            body += struct.pack("<I", len(vals))
            ch = _TPL_SCALAR_FMT[spec][1]
            body += struct.pack(f"<{len(vals)}{ch}", *vals)
        elif kind == "struct_array":
            # All observed struct fields are 'u' (float32) — same
            # assumption reader.parse_tpl makes when decoding.
            sig_parts.append(f"A(S({spec}))")
            width = len(spec)
            flat = [x for t in vals for x in t]
            if len(flat) != width * len(vals):
                raise WireError(f"struct values do not match spec {spec!r}")
            body += struct.pack("<I", len(vals))
            body += struct.pack(f"<{len(flat)}f", *flat)
        else:
            raise WireError(f"unknown tpl section kind {kind!r}")
    sig = "".join(sig_parts).encode("ascii")
    total = 8 + len(sig) + 1 + len(body)
    return b"tpl\x00" + struct.pack("<I", total) + sig + b"\x00" + bytes(body)
