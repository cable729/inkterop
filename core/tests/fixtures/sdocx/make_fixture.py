"""Generate the synthetic Samsung Notes .sdocx fixture in this dir.

Byte layouts follow docs/formats/sdocx.md (decoded from squ1dd13/
sdocx2pdf, MIT). The builders are the write-side mirror of
inkterop.formats.sdocx and are kept here (not in formats/) because no
sdocx writer is planned; tests import this module to build synthetic
stroke records too.

No oracle app exists on macOS, so the fixture is "valid" per the
decoded knowledge: hashes (note.note sha256, per-object
sha256(uuid+mtime)) and frame/flex offsets are computed properly so a
strict reference parser should accept it, but it has never been opened
in Samsung Notes [unknown].

Run: uv run python tests/fixtures/sdocx/make_fixture.py
"""
from __future__ import annotations

import hashlib
import struct
import zipfile
from pathlib import Path

HERE = Path(__file__).parent

PAGE_END_STRING = b"Page for SAMSUNG S-Pen SDK"
END_TAG_IDENT = b"Document for S-Pen SDK"
PEN = "com.samsung.android.sdk.pen.pen.preload."

MTIME_US = 1_700_000_000_000_000  # fixed so the fixture is reproducible


def bitfield(value: int) -> bytes:
    if value == 0:
        return b"\x00"
    n = (value.bit_length() + 7) // 8
    return bytes([n]) + value.to_bytes(n, "little")


def short_u16_str(s: str) -> bytes:
    return struct.pack("<H", len(s)) + s.encode("utf-16-le")


def short_u8_str(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<H", len(b)) + b


def frame(body: bytes) -> bytes:
    """Inclusive-size frame: u32 total size + body."""
    return struct.pack("<I", len(body) + 4) + body


# ------------------------------------------------------- fixed-point deltas

def encode_point_delta(v: float) -> int:
    """sign + 10-bit int + 5-bit fraction. v must be a multiple of 1/32."""
    a = abs(v)
    q = round(a * 32)
    assert q == a * 32 and q < (1 << 15), f"{v} not representable"
    return q | (0x8000 if v < 0 else 0)


def encode_small_delta(v: float) -> int:
    """sign + 3-bit int + 12-bit fraction. v must be a multiple of 1/4096."""
    a = abs(v)
    q = round(a * 4096)
    assert q == a * 4096 and q < (1 << 15), f"{v} not representable"
    return q | (0x8000 if v < 0 else 0)


# ------------------------------------------------------------ stroke object

def object_base(uuid: str, rect: tuple[float, float, float, float]) -> bytes:
    body = bytearray()
    body += struct.pack("<H", 0)  # data type: ObjectBase
    body += struct.pack("<I", 0)  # flex offset 0 => no optional fields
    body += bitfield(0b1110)  # selectable / movable / visible
    body += bitfield(0)
    body += struct.pack("<I", 1)  # format version
    body += short_u8_str(uuid)
    body += struct.pack("<q", MTIME_US)
    body += struct.pack("<4d", *rect)  # left, top, right, bottom
    body += struct.pack("<I", 0)  # timestamp int
    body += bytes([0])  # resize mode: free
    return frame(bytes(body))


def _uncompressed_events(points, pressures, timestamps, tilt, orientation):
    out = bytearray()
    for x, y in points:
        out += struct.pack("<2d", x, y)
    out += struct.pack(f"<{len(pressures)}f", *pressures)
    out += struct.pack(f"<{len(timestamps)}I", *timestamps)
    if tilt is not None:
        out += struct.pack(f"<{len(tilt)}f", *tilt)
        out += struct.pack(f"<{len(orientation)}f", *orientation)
    return bytes(out)


def _compressed_events(points, pressures, timestamps, tilt, orientation):
    n = len(points) - 1
    out = bytearray(struct.pack("<2d", *points[0]))
    for i in range(n):
        out += struct.pack(
            "<2H",
            encode_point_delta(points[i + 1][0] - points[i][0]),
            encode_point_delta(points[i + 1][1] - points[i][1]))
    out += struct.pack("<f", pressures[0])
    out += struct.pack(f"<{n}H", *(
        encode_small_delta(pressures[i + 1] - pressures[i])
        for i in range(n)))
    out += struct.pack("<I", timestamps[0])
    out += struct.pack(f"<{n}H", *(
        timestamps[i + 1] - timestamps[i] for i in range(n)))
    if tilt is not None:
        out += struct.pack("<f", tilt[0])
        out += struct.pack(f"<{n}H", *(
            encode_small_delta(tilt[i + 1] - tilt[i]) for i in range(n)))
        out += struct.pack("<f", orientation[0])
        out += struct.pack(f"<{n}H", *(
            encode_small_delta(orientation[i + 1] - orientation[i])
            for i in range(n)))
    return bytes(out)


def stroke_object(uuid: str, points, pressures, timestamps, *,
                  pen_name_id: int, color_bgra: bytes, pen_size: float,
                  compressed: bool = False, tilt=None, orientation=None,
                  tool_type: int = 2) -> bytes:
    """A full type-1 object entry payload (base + stroke frame + hash)."""
    events = (_compressed_events if compressed else _uncompressed_events)(
        points, pressures, timestamps, tilt, orientation)

    props = (1 if compressed else 0) | ((1 << 2) if tilt is not None else 0)
    fields = (1 << 2) | (1 << 3) | (1 << 7)  # colour, pen size, pen name
    pre_flex = (struct.pack("<H", len(points)) + events
                + struct.pack("<H", tool_type))
    head_len = 2 + 4 + len(bitfield(props)) + len(bitfield(fields))
    flex_off = 4 + head_len + len(pre_flex)

    body = (struct.pack("<H", 1)  # data type: stroke
            + struct.pack("<I", flex_off)
            + bitfield(props) + bitfield(fields)
            + pre_flex
            + color_bgra + struct.pack("<f", pen_size)
            + struct.pack("<I", pen_name_id))

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    rect = (min(xs), min(ys), max(xs), max(ys))
    obj_hash = hashlib.sha256(f"{uuid}{MTIME_US}".encode()).digest()
    return object_base(uuid, rect) + frame(body) + obj_hash


# -------------------------------------------------------------- page member

def layer(objects: list[bytes], name: str = "") -> bytes:
    header_body = bytearray()
    header_body += struct.pack("<I", 0)  # flex offset patched below
    header_body += bitfield(0)  # props: visible, unlocked
    fields = (1 << 2) if name else 0
    header_body += bitfield(fields)
    header_body += struct.pack("<I", 1)  # layer id
    if name:
        header_body += short_u16_str(name)
    header = bytearray(frame(bytes(header_body)))
    # Layer flex offsets are absolute in the page stream; the caller
    # patches them, so store the offset of the name field (or header
    # end) relative to the layer start here.
    name_off = len(header) - (len(short_u16_str(name)) if name else 0)
    struct.pack_into("<I", header, 4, name_off)

    out = bytearray(header)
    out += struct.pack("<I", len(objects))
    for obj in objects:
        out += bytes([1])  # object type: stroke
        out += struct.pack("<H", 0)  # child count
        out += struct.pack("<I", len(obj))
        out += obj
    out += hashlib.sha256(bytes(out)).digest()  # layer hash
    return bytes(out)


def _patch_layer_flex(page: bytearray, layer_start: int) -> None:
    rel = struct.unpack_from("<I", page, layer_start + 4)[0]
    struct.pack_into("<I", page, layer_start + 4, layer_start + rel)


def page_member(uuid: str, width: int, height: int,
                layers: list[bytes]) -> bytes:
    head = bytearray()
    head += struct.pack("<II", 0, 0)  # page end + flex offsets, patched
    head += bitfield(0)  # props
    head += bitfield(0)  # fields (none => flex == page end)
    head += struct.pack("<5I", 0, width, height, 0, 0)  # orient, w, h, x, y
    head += short_u16_str(uuid)
    head += struct.pack("<q", MTIME_US)
    head += struct.pack("<II", 1, 1)  # format / min format version
    struct.pack_into("<II", head, 0, len(head), len(head))

    out = bytearray(head)
    out += struct.pack("<HH", len(layers), 0)  # count, current index
    for lay in layers:
        _patch_layer_flex_bytes = len(out)
        out += lay
        _patch_layer_flex(out, _patch_layer_flex_bytes)
    out += hashlib.sha256(bytes(out)).digest()  # page hash
    out += PAGE_END_STRING
    return bytes(out)


def page_hash(page: bytes) -> bytes:
    return page[-(32 + len(PAGE_END_STRING)):-len(PAGE_END_STRING)]


# ------------------------------------------------------------ note.note etc

def note_note(width: int, height: int, strings: dict[int, str]) -> bytes:
    body = bytearray()
    body += struct.pack("<I", 0)  # flex offset, patched below
    body += bitfield(0)  # props
    body += bitfield(1 << 10)  # fields: string registry only
    body += struct.pack("<I", 1)  # format version
    body += short_u16_str("fixture-doc")
    body += struct.pack("<I", 1)  # file revision
    body += struct.pack("<qq", MTIME_US, MTIME_US)  # created, modified
    body += struct.pack("<5I", width, height, 0, 0, 1)  # w h hpad vpad minver
    body += struct.pack("<I", 0)  # title text: 0 bytes
    body += struct.pack("<I", 0)  # body text: 0 bytes
    struct.pack_into("<I", body, 0, len(body))

    reg = bytearray(struct.pack("<H", len(strings)))
    for key, value in strings.items():
        reg += struct.pack("<I", key) + short_u16_str(value)
    body += struct.pack("<I", len(reg)) + reg
    return bytes(body) + hashlib.sha256(bytes(body)).digest()


def page_id_info(note: bytes, pages: list[bytes], uuids: list[str]) -> bytes:
    out = bytearray(note[-32:])  # note.note hash
    out += struct.pack("<H", len(pages))
    for uuid, page in zip(uuids, pages):
        out += short_u16_str(uuid) + page_hash(page)
    return bytes(out)


def end_tag(width: int, height: int) -> bytes:
    body = bytearray()
    body += struct.pack("<I", 1)  # format version
    body += short_u16_str("fixture-doc")
    body += struct.pack("<q", MTIME_US)
    body += struct.pack("<I", 0)  # property flags (portrait)
    body += short_u16_str("")  # cover image
    body += struct.pack("<If", width, float(height))
    body += short_u16_str("inkterop-fixture")  # app name
    body += struct.pack("<II", 1, 0) + short_u16_str("")  # app version
    body += struct.pack("<I", 1)  # min format version
    body += struct.pack("<q", MTIME_US)  # created
    body += struct.pack("<I", 0)  # last viewed page
    body += struct.pack("<HH", 0, 0)  # page model: paged; doc type
    body += short_u16_str("")  # owner id
    body += struct.pack("<II", 0, 0)  # skip block, encryption block
    body += struct.pack("<3q", MTIME_US, MTIME_US, MTIME_US)
    body += short_u16_str("")  # fixed font
    body += struct.pack("<II", 2, 2)  # text direction, theme: defaults
    body += struct.pack("<q", 0)  # server check point
    body += struct.pack("<II", 0, 1)  # orientation, min unknown version
    body += struct.pack("<I", 0)  # app custom data: empty long string
    body += END_TAG_IDENT
    return struct.pack("<H", len(body)) + bytes(body)


# ----------------------------------------------------------------- document

def build_fixture() -> bytes:
    """Two pages: page 1 = uncompressed fountain pen (with tilt) +
    highlighter; page 2 = delta-compressed pen stroke + single dot."""
    import io

    width, height = 1440, 2038
    strings = {
        1: PEN + "FountainPen",
        2: PEN + "Marker4",
        3: PEN + "Pencil2",
    }

    pen = stroke_object(
        "aaaaaaaa-0000-0000-0000-000000000001",
        points=[(100.0, 200.0), (150.0, 260.0), (220.0, 250.0)],
        pressures=[0.3, 0.8, 0.5], timestamps=[0, 12, 25],
        tilt=[0.4, 0.45, 0.5], orientation=[1.0, 1.1, 1.2],
        pen_name_id=1, color_bgra=bytes([0x30, 0x20, 0x10, 0xFF]),
        pen_size=12.0)
    highlight = stroke_object(
        "aaaaaaaa-0000-0000-0000-000000000002",
        points=[(100.0, 400.0), (600.0, 400.0)],
        pressures=[0.5, 0.5], timestamps=[0, 40],
        pen_name_id=2, color_bgra=bytes([0x00, 0xE0, 0xFF, 0x59]),
        pen_size=20.0)
    compressed = stroke_object(
        "aaaaaaaa-0000-0000-0000-000000000003",
        points=[(300.0, 300.0), (302.5, 298.0), (306.25, 297.5)],
        pressures=[0.5, 0.625, 0.75], timestamps=[0, 8, 16],
        pen_name_id=3, color_bgra=bytes([0xFF, 0x00, 0x00, 0xFF]),
        pen_size=4.0, compressed=True)
    dot = stroke_object(
        "aaaaaaaa-0000-0000-0000-000000000004",
        points=[(50.0, 50.0)], pressures=[0.9], timestamps=[0],
        pen_name_id=1, color_bgra=bytes([0x00, 0x00, 0x00, 0xFF]),
        pen_size=6.0)

    uuids = ["11111111-2222-3333-4444-555555555555",
             "66666666-7777-8888-9999-aaaaaaaaaaaa"]
    pages = [
        page_member(uuids[0], width, height,
                    [layer([pen, highlight], name="Layer 1")]),
        page_member(uuids[1], width, height, [layer([compressed, dot])]),
    ]
    note = note_note(width, height, strings)

    members = [
        ("pageIdInfo.dat", page_id_info(note, pages, uuids)),
        *((f"{uuid}.page", page) for uuid, page in zip(uuids, pages)),
        ("media/mediaInfo.dat", struct.pack("<H", 0) + b"EOF"),
        ("note.note", note),
        ("end_tag.bin", end_tag(width, height)),
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members:
            # Fixed timestamp so the fixture is byte-reproducible.
            info = zipfile.ZipInfo(name, date_time=(2023, 11, 14, 22, 13, 20))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return buf.getvalue()


def main() -> None:
    out = HERE / "synthetic-two-page.sdocx"
    out.write_bytes(build_fixture())
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
