#!/usr/bin/env python3
"""Apple libcompression framed LZ4 decoder (pure stdlib, independent impl).

Frame format (Apple's `compression_encode_buffer` with COMPRESSION_LZ4;
documented by Apple and widely described):
  repeated blocks:
    b"bv41" + u32le decompressed_size + u32le compressed_size + LZ4 block
    b"bv4-" + u32le size + raw bytes                (uncompressed block)
  terminator: b"bv4$"

LZ4 *block* format (https://github.com/lz4/lz4/blob/dev/doc/lz4_Block_format.md
— format spec, not code): sequences of
  token (hi nibble = literal length, lo nibble = match length - 4),
  0xFF-extension bytes, literals, u16le match offset, match copy (may overlap).

Usage: applelz4.py IN OUT  (or import decompress/lz4_block_decompress)
"""
from __future__ import annotations

import struct
import sys


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
        if pos >= n:  # last sequence has no match part
            break
        offset = struct.unpack_from("<H", src, pos)[0]
        pos += 2
        if offset == 0:
            raise ValueError("LZ4 match offset 0")
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
            raise ValueError("LZ4 match before start of output")
        for i in range(match_len):  # byte-wise: matches may overlap
            out.append(out[start + i])
    if len(out) != expected_size:
        raise ValueError(f"LZ4 block: got {len(out)}B, expected {expected_size}B")
    return bytes(out)


def decompress(buf: bytes) -> bytes:
    """Decode a whole Apple-framed LZ4 stream."""
    out = bytearray()
    pos = 0
    while pos < len(buf):
        magic = buf[pos:pos + 4]
        if magic == b"bv4$":
            break
        if magic == b"bv41":
            dec_size, comp_size = struct.unpack_from("<II", buf, pos + 4)
            block = buf[pos + 12:pos + 12 + comp_size]
            out += lz4_block_decompress(block, dec_size)
            pos += 12 + comp_size
        elif magic == b"bv4-":
            size = struct.unpack_from("<I", buf, pos + 4)[0]
            out += buf[pos + 8:pos + 8 + size]
            pos += 8 + size
        else:
            raise ValueError(f"unknown block magic {magic!r} at {pos}")
    return bytes(out)


def find_frames(buf: bytes) -> list[tuple[int, bytes]]:
    """Locate and decode every bv41 frame inside a larger blob."""
    frames = []
    pos = 0
    while (idx := buf.find(b"bv41", pos)) != -1:
        end = buf.find(b"bv4$", idx)
        if end == -1:
            break
        frames.append((idx, decompress(buf[idx:end + 4])))
        pos = end + 4
    return frames


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    data = open(sys.argv[1], "rb").read()
    open(sys.argv[2], "wb").write(decompress(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
