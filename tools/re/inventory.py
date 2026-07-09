#!/usr/bin/env python3
"""First tool to run on any unknown note file: what's inside?

Walks a zip (or directory), reporting per member: size, magic-byte type
guess, and Shannon entropy (high entropy without a known magic suggests
compression or encryption). Pure stdlib.

Usage: inventory.py FILE_OR_DIR
"""
from __future__ import annotations

import math
import sys
import zipfile
from collections import Counter
from pathlib import Path

MAGICS = [
    (b"PK\x03\x04", "zip"),
    (b"bplist00", "binary plist"),
    (b"%PDF", "pdf"),
    (b"\x89PNG", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x1f\x8b", "gzip"),
    (b"bv41", "apple-lz4 frame"),
    (b"bv4-", "apple-lz4 raw frame"),
    (b"tpl\x00", "goodnotes tpl block"),
    (b"noteSN_FILE_VER_", "supernote"),
    (b"reMarkable .lines", "remarkable lines"),
    (b"<?xml", "xml"),
    (b"{", "json?"),
]


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    n = len(data)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def sniff(data: bytes) -> str:
    for magic, name in MAGICS:
        if data.startswith(magic):
            return name
    if data[:64] and looks_like_protobuf(data):
        return "protobuf?"
    return "unknown"


def looks_like_protobuf(data: bytes) -> bool:
    """Cheap check: does it start with a plausible field key + payload?"""
    try:
        key = data[0]
        wtype = key & 7
        number = key >> 3
        return 1 <= number <= 200 and wtype in (0, 1, 2, 5)
    except IndexError:
        return False


def describe(name: str, data: bytes) -> str:
    e = entropy(data)
    kind = sniff(data)
    head = data[:16].hex()
    return f"{name:50s} {len(data):>10,}B  H={e:4.2f}  {kind:22s} {head}"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    target = Path(sys.argv[1])
    if target.is_dir():
        for p in sorted(target.rglob("*")):
            if p.is_file():
                print(describe(str(p.relative_to(target)), p.read_bytes()))
        return 0
    raw = target.read_bytes()
    if raw[:4] == b"PK\x03\x04":
        print(f"{target.name}: zip archive")
        with zipfile.ZipFile(target) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                print(describe(info.filename, z.read(info)))
    else:
        print(describe(target.name, raw))
    return 0


if __name__ == "__main__":
    sys.exit(main())
