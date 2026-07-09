"""Generate the synthetic Onyx Boox .note fixture in this dir.

Builds a minimal single-note Boox archive from the decoded format facts
in docs/formats/boox.md (container layout, #points blob, shape
protobuf): one 1860x2480 page with a ballpoint stroke, a pressure-varied
fountain stroke, a translated highlighter stroke, and a text box.

Run: uv run python tests/fixtures/boox/make_fixture.py
"""
from __future__ import annotations

import io
import json
import struct
import zipfile
from pathlib import Path

HERE = Path(__file__).parent

NOTE_ID = "0f9c2b1a4e6d48d2a1b3c5d7e9f01234"
PAGE_ID = "11112222333344445555666677778888"
POINTS_DOC_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SHAPE_DOC_ID = "99998888-7777-6666-5555-444433332222"
PAGE_W, PAGE_H = 1860.0, 2480.0

BALLPOINT_UUID = "10000000-0000-4000-8000-000000000001"
FOUNTAIN_UUID = "10000000-0000-4000-8000-000000000002"
HIGHLIGHT_UUID = "10000000-0000-4000-8000-000000000003"
TEXT_UUID = "10000000-0000-4000-8000-000000000004"


# --- protobuf encoding helpers ----------------------------------------------

def varint(v: int) -> bytes:
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def tag(number: int, wtype: int) -> bytes:
    return varint((number << 3) | wtype)


def pb_varint(number: int, v: int) -> bytes:
    return tag(number, 0) + varint(v)


def pb_bytes(number: int, payload: bytes | str) -> bytes:
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return tag(number, 2) + varint(len(payload)) + payload


def pb_f32(number: int, v: float) -> bytes:
    return tag(number, 5) + struct.pack("<f", v)


# --- #points blob ------------------------------------------------------------

def points_blob(strokes: list[tuple[str, list[tuple]]]) -> bytes:
    """strokes: [(shapeUUID, [(x, y, tilt_x, tilt_y, pressure, t_ms)])]."""
    out = bytearray()
    out += struct.pack(">I", 1)
    out += PAGE_ID.encode("ascii").ljust(36)  # condensed + space padded
    out += POINTS_DOC_ID.encode("ascii")
    index = []
    for uuid, pts in strokes:
        offset = len(out)
        out += b"\x00\x00\x00\x00"
        for p in pts:
            out += struct.pack(">ffBBHI", *p)
        index.append((uuid, offset, len(out) - offset))
    index_start = len(out)
    for uuid, offset, size in index:
        out += uuid.encode("ascii")
        out += struct.pack(">II", offset, size)
    out += struct.pack(">I", index_start)
    return bytes(out)


# --- shape protobuf (nested zip) ----------------------------------------------

def shape_message(uuid: str, pen_type: int, argb: int, thickness: float,
                  created: int, bbox: tuple[float, float, float, float],
                  matrix: list[float] | None = None,
                  text: str | None = None) -> bytes:
    left, top, right, bottom = bbox
    msg = pb_bytes(1, uuid)
    msg += pb_varint(2, created)
    msg += pb_varint(3, created)
    msg += pb_varint(4, argb)  # unsigned; device emits sign-extended int64
    msg += pb_f32(5, thickness)
    msg += pb_varint(6, 0)  # layer id
    msg += pb_bytes(7, json.dumps({"left": left, "top": top, "right": right,
                                   "bottom": bottom, "empty": False,
                                   "stability": 0}))
    if matrix is not None:
        msg += pb_bytes(8, json.dumps({"values": matrix}))
    if text is not None:
        msg += pb_bytes(9, json.dumps({"textSize": 32, "alignType": 0}))
        msg += pb_bytes(10, text)
    msg += pb_bytes(11, json.dumps({"maxPressure": 4095.0, "dpi": 320.0}))
    msg += pb_varint(12, pen_type)
    msg += pb_bytes(16, POINTS_DOC_ID)
    msg += pb_bytes(18, SHAPE_DOC_ID)
    return pb_bytes(1, msg)


def shape_zip(messages: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{PAGE_ID}#{SHAPE_DOC_ID}#1000000000000", messages)
    return buf.getvalue()


# --- note_info / pageModel -----------------------------------------------------

def note_info() -> bytes:
    canvas_state = {
        "defaultPageRect": {"left": 0, "top": 0, "right": PAGE_W,
                            "bottom": PAGE_H, "empty": False},
        "pageInfoMap": {PAGE_ID: {
            "width": PAGE_W, "height": PAGE_H, "currentLayerId": 0,
            "layerList": [{"id": 0, "lock": False, "show": True}],
        }},
    }
    meta = pb_bytes(1, NOTE_ID)
    meta += pb_varint(2, 1000000000000)
    meta += pb_varint(3, 1000000000000)
    meta += pb_bytes(6, "boox synthetic")
    meta += pb_bytes(12, json.dumps(canvas_state))
    meta += pb_bytes(14, json.dumps({"deviceName": "synthetic",
                                     "size": {"width": PAGE_W,
                                              "height": PAGE_H}}))
    meta += pb_bytes(20, json.dumps({"pageNameList": [PAGE_ID]}))
    meta += pb_f32(22, PAGE_W)
    meta += pb_f32(23, PAGE_H)
    return pb_bytes(1, meta)  # note_info wraps the metadata at field 1


def page_model() -> bytes:
    sub = pb_bytes(1, PAGE_ID)
    sub += pb_bytes(2, json.dumps(
        {"layerList": [{"id": 0, "lock": False, "show": True}]}))
    sub += pb_varint(5, 1000000000000)
    sub += pb_varint(6, 1000000000000)
    sub += pb_bytes(7, json.dumps({"left": 0.0, "top": 0.0, "right": PAGE_W,
                                   "bottom": PAGE_H, "empty": False}))
    return pb_bytes(1, sub)


# --- assembly -------------------------------------------------------------------

def build() -> bytes:
    ballpoint = [(100.0 + 10.0 * i, 200.0 + 5.0 * i, 20, 25, 2000, 4 * i)
                 for i in range(20)]
    fountain = [(100.0 + 12.0 * i, 400.0 + 3.0 * i, 30, 28,
                 500 + 150 * i, 5 * i) for i in range(20)]
    # highlighter drawn at y=0, translated to y=600 by the shape matrix
    highlight = [(100.0 + 25.0 * i, 0.0, 0, 0, 4095, 6 * i)
                 for i in range(10)]

    blob = points_blob([
        (BALLPOINT_UUID, ballpoint),
        (FOUNTAIN_UUID, fountain),
        (HIGHLIGHT_UUID, highlight),
    ])
    shapes = b"".join([
        shape_message(BALLPOINT_UUID, 2, 0xFF0000FF, 4.0, 1000000000001,
                      (100.0, 200.0, 290.0, 295.0)),
        shape_message(FOUNTAIN_UUID, 5, 0xFF000000, 6.0, 1000000000002,
                      (100.0, 400.0, 328.0, 457.0)),
        shape_message(HIGHLIGHT_UUID, 15, 0xFFFFDD00, 40.0, 1000000000003,
                      (100.0, 600.0, 325.0, 600.0),
                      matrix=[1.0, 0.0, 0.0, 0.0, 1.0, 600.0, 0.0, 0.0, 1.0]),
        shape_message(TEXT_UUID, 6, 0xFF000000, 1.0, 1000000000004,
                      (100.0, 800.0, 500.0, 840.0), text="synthetic boox"),
    ])

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{NOTE_ID}/note/pb/note_info", note_info())
        zf.writestr(f"{NOTE_ID}/pageModel/pb/{PAGE_ID}", page_model())
        zf.writestr(
            f"{NOTE_ID}/point/{PAGE_ID}/{PAGE_ID}#{POINTS_DOC_ID}#points",
            blob)
        zf.writestr(
            f"{NOTE_ID}/shape/{PAGE_ID}#{SHAPE_DOC_ID}#1000000000000.zip",
            shape_zip(shapes))
        zf.writestr(f"{NOTE_ID}/extra/pb/extra", pb_varint(1, 1))
    return buf.getvalue()


if __name__ == "__main__":
    out = HERE / "boox-synthetic.note"
    out.write_bytes(build())
    print(f"wrote {out} ({out.stat().st_size} bytes)")
