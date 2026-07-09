"""IR -> reMarkable .rm (v6 page) and .rmdoc (import container).

Blocks are emitted via rmscene's write_blocks with the minimal sequence
its own simple_text_document uses (AuthorIds, MigrationInfo, PageInfo,
SceneTree, TreeNode root + one TreeNode/SceneGroupItem per IR layer,
then SceneLineItemBlocks) [inferred — mirrors rmscene 0.8.0; the block
set a real device/app requires is unverified until the .rmdoc ground
truth diff]. Point mapping is the exact inverse of reader.py:

  point.width    = round(WIDTH_units * 4)      (the /4 fidelity rule)
  point.pressure = round(PRESSURE * 255)
  point.speed    = round(SPEED)
  point.direction= round(TILT_AZIMUTH * 255 / 2pi) % 256

Native round-trips (NativeTool format_id == "remarkable") keep
Pen/PenColor/color_rgba/thickness_scale and device-space coordinates
verbatim. Foreign documents are fit to canvas width (aspect preserved,
x centered on 0, y from 0 growing past nominal height as needed —
"adjustable page height"). This is the one format honoring all three
fidelities; the ALPHA channel is dropped (device re-derives it).

.rmdoc = zip of <uuid>.metadata + <uuid>.content + <uuid>/<page>.rm
(members [inferred] from docs/formats/remarkable.md cache layout; to be
diffed against a real desktop-app export). Validation is ONLY via the
desktop app's UI import — never write the cache (deny-listed).
"""
from __future__ import annotations

import io
import json
import math
import time
import uuid as uuidlib
import zipfile
from pathlib import Path
from statistics import median
from typing import Any

import rmscene.scene_items as si
from rmscene import CrdtId, LwwValue
from rmscene.crdt_sequence import CrdtSequenceItem
from rmscene.scene_stream import (
    AuthorIdsBlock,
    MigrationInfoBlock,
    PageInfoBlock,
    SceneGroupItemBlock,
    SceneLineItemBlock,
    SceneTreeBlock,
    TreeNodeBlock,
    write_blocks,
)

from ... import ir
from ..base import Fidelity
from .pens import RM_PALETTE
from .reader import CANVAS_H, CANVAS_W, FORMAT_ID, _FAMILY

# Inverse of reader._FAMILY, preferring the _2 (current-gen) pens.
_FAMILY_PEN = {
    ir.ToolFamily.BALLPOINT: si.Pen.BALLPOINT_2,
    ir.ToolFamily.CALLIGRAPHY: si.Pen.CALIGRAPHY,
    ir.ToolFamily.ERASER: si.Pen.ERASER,
    ir.ToolFamily.FINELINER: si.Pen.FINELINER_2,
    ir.ToolFamily.HIGHLIGHTER: si.Pen.HIGHLIGHTER_2,
    ir.ToolFamily.MARKER: si.Pen.MARKER_2,
    ir.ToolFamily.MECHANICAL_PENCIL: si.Pen.MECHANICAL_PENCIL_2,
    ir.ToolFamily.BRUSH: si.Pen.PAINTBRUSH_2,
    ir.ToolFamily.PENCIL: si.Pen.PENCIL_2,
    ir.ToolFamily.SHADER: si.Pen.SHADER,
    ir.ToolFamily.PEN: si.Pen.BALLPOINT_2,
    ir.ToolFamily.UNKNOWN: si.Pen.FINELINER_2,
}


def _nearest_pen_color(color: ir.Color) -> si.PenColor:
    def dist(rgb: tuple[float, float, float]) -> float:
        return ((rgb[0] - color.r) ** 2 + (rgb[1] - color.g) ** 2
                + (rgb[2] - color.b) ** 2)
    return min(RM_PALETTE, key=lambda pc: dist(RM_PALETTE[pc]))


def _native_transform(page: ir.Page) -> tuple[float, float, float]:
    """(scale, dx, dy) source -> device space for a native rM page."""
    return 1.0, 0.0, 0.0


def _foreign_transform(page: ir.Page) -> tuple[float, float, float]:
    """Fit the page to canvas width, center x on 0, y from 0."""
    b = page.bounds
    k = CANVAS_W / b.width if b.width else 1.0
    return k, -b.x_min * k - CANVAS_W / 2.0, -b.y_min * k


def _line_from_stroke(s: ir.Stroke, k: float, dx: float, dy: float) -> si.Line:
    native = s.tool.native if (s.tool and s.tool.native
                               and s.tool.native.format_id == FORMAT_ID) else None
    n = len(s.x)
    widths = s.channels.get(ir.Channel.WIDTH)
    if widths is None:
        base = (s.appearance.width if s.appearance and s.appearance.width
                else 2.0)
        widths = [base] * n
    pressures = s.channels.get(ir.Channel.PRESSURE) or [0.5] * n
    speeds = s.channels.get(ir.Channel.SPEED) or [0.0] * n
    azimuths = s.channels.get(ir.Channel.TILT_AZIMUTH) or [0.0] * n

    points = [
        si.Point(
            x=float(x * k + dx),
            y=float(y * k + dy),
            speed=round(speeds[i]),
            direction=round(azimuths[i] * 255.0 / (2 * math.pi)) % 256,
            width=max(0, round(widths[i] * k * 4.0)),
            pressure=min(255, max(0, round(pressures[i] * 255.0))),
        )
        for i, (x, y) in enumerate(zip(s.x, s.y))
    ]

    if native is not None:
        pen = si.Pen(int(native.tool_id))
        color = si.PenColor(int(native.params.get("color", 0)))
        rgba = native.params.get("color_rgba")
        color_rgba = tuple(rgba) if rgba else None
        thickness = float(native.params.get("thickness_scale", 1.0))
    else:
        family = s.tool.family if s.tool else ir.ToolFamily.UNKNOWN
        pen = _FAMILY_PEN.get(family, si.Pen.FINELINER_2)
        render = s.appearance.color if s.appearance else s.color
        opacity = s.appearance.opacity if s.appearance else 1.0
        color = _nearest_pen_color(render)
        color_rgba = (round(render.r * 255), round(render.g * 255),
                      round(render.b * 255), round(opacity * 255))
        thickness = 1.0

    return si.Line(
        color=color,
        tool=pen,
        points=points,
        thickness_scale=thickness,
        starting_length=0.0,
        color_rgba=color_rgba,
    )


def page_to_blocks(page: ir.Page, author: uuidlib.UUID | None = None):
    """Minimal v6 block sequence for one IR page."""
    yield AuthorIdsBlock(author_uuids={1: author or uuidlib.uuid4()})
    yield MigrationInfoBlock(migration_id=CrdtId(1, 1), is_device=True)
    yield PageInfoBlock(loads_count=1, merges_count=0,
                        text_chars_count=1, text_lines_count=1)
    yield TreeNodeBlock(si.Group(node_id=CrdtId(0, 1)))

    native = bool(page.extra.get(FORMAT_ID))
    k, dx, dy = (_native_transform(page) if native
                 else _foreign_transform(page))

    layers = [ly for ly in (page.layers or [ir.Layer()]) if ly.visible]
    item_seq = 20
    layer_blocks = []
    for li, layer in enumerate(layers):
        node = CrdtId(0, 11 + 3 * li)
        # every layer group must be announced in the scene tree first
        yield SceneTreeBlock(tree_id=node, node_id=CrdtId(0, 0),
                             is_update=True, parent_id=CrdtId(0, 1))
        yield TreeNodeBlock(si.Group(
            node_id=node,
            label=LwwValue(CrdtId(0, 12 + 3 * li), layer.name or f"Layer {li + 1}"),
        ))
        yield SceneGroupItemBlock(
            parent_id=CrdtId(0, 1),
            item=CrdtSequenceItem(
                item_id=CrdtId(0, 13 + 3 * li),
                left_id=CrdtId(0, 0), right_id=CrdtId(0, 0),
                deleted_length=0, value=node,
            ),
        )
        for s in layer.strokes:
            if not s.x:
                continue
            layer_blocks.append(SceneLineItemBlock(
                parent_id=node,
                item=CrdtSequenceItem(
                    item_id=CrdtId(1, item_seq),
                    left_id=CrdtId(0, 0), right_id=CrdtId(0, 0),
                    deleted_length=0,
                    value=_line_from_stroke(s, k, dx, dy),
                ),
            ))
            item_seq += 1
    yield from layer_blocks


def write_rm_page(page: ir.Page, path_or_buf) -> None:
    if hasattr(path_or_buf, "write"):
        write_blocks(path_or_buf, page_to_blocks(page))
    else:
        with open(path_or_buf, "wb") as f:
            write_blocks(f, page_to_blocks(page))


class RemarkablePageWriter:
    """Single-page bare .rm v6 file."""

    format_id = FORMAT_ID
    extensions = (".rm",)
    validated = False  # pending desktop-app import check (via .rmdoc)

    def write(self, doc: ir.Document, path: Path, fidelity: Fidelity,
              options: dict[str, Any] | None = None) -> None:
        idx = int((options or {}).get("page", 0))
        if len(doc.pages) != 1 and "page" not in (options or {}):
            raise ValueError(
                f".rm holds one page; document has {len(doc.pages)} "
                f"(pass options={{'page': i}} or write .rmdoc)"
            )
        write_rm_page(doc.pages[idx], path)


class RmdocWriter:
    """.rmdoc container (desktop-app File > Import)."""

    format_id = FORMAT_ID
    extensions = (".rmdoc",)
    validated = False  # pending desktop-app import check

    def write(self, doc: ir.Document, path: Path, fidelity: Fidelity,
              options: dict[str, Any] | None = None) -> None:
        doc_uuid = str(uuidlib.uuid4())
        page_uuids = [str(uuidlib.uuid4()) for _ in doc.pages]
        now = str(int(time.time() * 1000))

        author = uuidlib.uuid4()
        rm_payloads: list[bytes] = []
        for page in doc.pages:
            buf = io.BytesIO()
            write_blocks(buf, page_to_blocks(page, author=author))
            rm_payloads.append(buf.getvalue())

        # Member set and JSON structure replicate a real desktop-cache
        # document field-for-field (observed 2026-07-09; the previous
        # minimal {fileType, orientation, pageCount, cPages.pages
        # [id,template]} container was rejected by desktop File > Import
        # with "No such file or directory").
        metadata = {
            "createdTime": now,
            "lastModified": now,
            "lastOpened": now,
            "lastOpenedPage": 0,
            "new": False,
            "parent": "",
            "pinned": False,
            "source": "",
            "type": "DocumentType",
            "visibleName": doc.title or path.stem,
        }
        orientation = doc.orientation or "portrait"
        content = {
            "cPages": {
                "lastOpened": {"timestamp": "1:1", "value": page_uuids[0]},
                "original": {"timestamp": "0:0", "value": -1},
                "pages": [
                    {
                        "id": pu,
                        "idx": {"timestamp": "2:2",
                                "value": _fractional_index(i)},
                        "modifed": now,  # (sic — field name as observed)
                        "template": {"timestamp": "2:1", "value": "Blank"},
                    }
                    for i, pu in enumerate(page_uuids)
                ],
                # CRDT author table mapping the author index used by the
                # AuthorIdsBlock in each .rm payload
                "uuids": [{"first": str(author), "second": 1}],
            },
            "coverPageNumber": -1,
            "customZoomCenterX": 0,
            "customZoomCenterY": 936,
            "customZoomOrientation": orientation,
            "customZoomPageHeight": 1872,
            "customZoomPageWidth": 1404,
            "customZoomScale": 1,
            "documentMetadata": {},
            "extraMetadata": {},
            "fileType": "notebook",
            "fontName": "",
            "formatVersion": 2,
            "lineHeight": -1,
            "orientation": orientation,
            "pageCount": len(doc.pages),
            "pageTags": [],
            "sizeInBytes": str(sum(len(b) for b in rm_payloads)),
            "tags": [],
            "textAlignment": "justify",
            "textScale": 1,
            "zoomMode": "bestFit",
        }
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{doc_uuid}.metadata", json.dumps(metadata, indent=4))
            zf.writestr(f"{doc_uuid}.content", json.dumps(content, indent=4))
            zf.writestr(f"{doc_uuid}.local",
                        json.dumps({"contentFormatVersion": 2}, indent=4))
            for pu, payload in zip(page_uuids, rm_payloads):
                zf.writestr(f"{doc_uuid}/{pu}.rm", payload)


def _fractional_index(i: int) -> str:
    """xochitl-style fractional-index page keys, lexicographically
    ordered: "ba", "bb", ... (a single-page cache doc shows "ba")."""
    prefix, letter = divmod(i, 25)
    return "b" + "z" * prefix + chr(ord("a") + letter)
