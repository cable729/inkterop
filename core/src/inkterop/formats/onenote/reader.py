"""OneNote 2016 ``.one`` (classic revision store) -> IR, ink first.

Container: MS-ONESTORE, parsed by ``onestore.py`` [verified against the
spec and the sample corpus]. The ink hierarchy inside it is NOT in the
public MS-ONE spec; the facts below come from the m-siemens.de write-up
"Decoding OneNote's File Format Secrets" (May 2026, prose facts), from
empirical inspection of the corpus samples, and from MS-ISF for the shared
number encodings. JCID/property-id VALUES were confirmed empirically
against the samples and are marked [inferred].

Ink hierarchy [inferred, validated on scaled_ink.one +
handwriting_recognition.one + desktop_missing_ink.one]:

  jcidInkContainer   0x00060014  OffsetFromParentHoriz/Vert (f32,
                                 half-inch), InkScalingX/Y (f32),
                                 InkData ref -> InkDataNode, or
                                 ContentChildNodes -> nested containers
  jcidInkDataNode    0x0002003B  InkStrokes (ref array), InkBoundingBox
                                 (4 x i32, unscaled ink units)
  jcidInkStrokeNode  0x00020047  InkPath (bytes), InkStrokeProperties ref
  jcidStrokePropertiesNode 0x00120048  InkDimensions (32 B/entry),
                                 InkColor (COLORREF), InkWidth/InkHeight
                                 (f32 HIMETRIC), InkPenTip,
                                 InkRasterOperation, InkTransparency

InkPath [verified on samples]: ISF multibyte encoding (7-bit groups,
sign-flip signed) shared with formats/isf.py. Layout: one signed multibyte
LENGTH (value count), then count signed multibyte DELTAS, dimension-major
(all X, all Y, then pressure...). Values are second-order-free plain deltas:
``coord[i] = coord[i-1] + delta[i]`` per dimension (first value absolute).
Delta (not absolute) decoding was confirmed by cumulative sums matching
page-plausible extents while raw values do not.

InkDimensions entry [inferred]: GUID (16) + i32 lower limit + i32 upper
limit + u32 unit + f32 resolution. Observed X/Y GUIDs are the ISF packet
property GUIDs {598A6A8F-52C0-4BA0-93AF-AF357411A561} (X) /
{B53F9F75-04E0-4498-A7EE-C30DBB5A9011} (Y) with unit=2 (cm),
resolution=1000/cm => native ink unit is HIMETRIC (0.01 mm), and pressure
{7307502D-F9F4-4E18-B3F2-2CE1B1A3610C} with limits [0, 32767].

Geometry mapping [inferred, plausibility-validated on scaled_ink.one where
one container carries InkScalingY=7.18 and OffsetFromParentVert=-19.03 and
both strokes land in the same page region only under this formula]:

  page_pt = offset_halfinch * 36 + cum_delta * ink_scaling * 72/2540

Page sizes/offsets in half-inch increments are documented ([MS-ONE] 2.3.18,
2.3.19); pages produced here use PDF points directly (point_scale = 1).

Honest subset: FSSHTTPB-packaged files are detected and rejected; ink
inside outline elements is placed with only the offsets present on its
ancestor chain (text-layout-derived positions are not reproduced); rich
text is extracted as plain unpositioned-run TextBlocks; embedded images
and file attachments are ignored.
"""
from __future__ import annotations

import logging
import struct
from itertools import accumulate
from pathlib import Path

from ... import ir
from ..isf import read_mbsint, read_mbuint
from . import onestore
from .onestore import (
    ExGuid,
    OneObject,
    OneStoreError,
    ObjectSpace,
    PT_OID,
    PT_OID_ARRAY,
    PT_U32,
    parse_onestore,
    read_guid,
)

_logger = logging.getLogger(__name__)

FORMAT_ID = "onenote"

HALF_INCH_PT = 36.0
HIMETRIC_PT = 72.0 / 2540.0

# JCIDs [inferred: undocumented; observed in every ink-bearing sample]
JCID_PAGE_MANIFEST = 0x00060037
JCID_PAGE_NODE = 0x0006000B
JCID_TITLE_NODE = 0x0006002C
JCID_OUTLINE_NODE = 0x0006000C
JCID_OUTLINE_ELEMENT = 0x0006000D
JCID_RICH_TEXT = 0x0006000E
JCID_INK_CONTAINER = 0x00060014
JCID_INK_DATA = 0x0002003B
JCID_INK_STROKE = 0x00020047
JCID_STROKE_PROPS = 0x00120048

# 26-bit property ids [inferred]
P_OFFSET_HORIZ = 0x1C14   # f32, half-inch
P_OFFSET_VERT = 0x1C15    # f32, half-inch
P_PAGE_WIDTH = 0x1C01     # f32, half-inch
P_PAGE_HEIGHT = 0x1C02    # f32, half-inch
P_CONTENT_CHILDREN = 0x1C1F
P_ELEMENT_CHILDREN = 0x1C20
P_CACHED_TITLE = 0x1D3C   # CachedTitleStringFromPage, UTF-16 bytes
P_RICH_TEXT_UNICODE = 0x1C22
P_INK_SCALING_X = 0x1C46  # f32
P_INK_SCALING_Y = 0x1C47  # f32
P_INK_STROKE_PROPERTIES = 0x3409
P_INK_DIMENSIONS = 0x340A
P_INK_PATH = 0x340B
P_INK_HEIGHT = 0x340C     # f32, HIMETRIC
P_INK_WIDTH = 0x340D      # f32, HIMETRIC
P_INK_COLOR = 0x340F      # COLORREF 0x00BBGGRR
P_INK_PEN_TIP = 0x3412
P_INK_RASTER_OP = 0x3413
P_INK_TRANSPARENCY = 0x3414
P_INK_DATA = 0x3415
P_INK_STROKES = 0x3416
P_INK_BOUNDING_BOX = 0x3418

# InkDimensions GUIDs (ISF packet-property GUIDs) [inferred]
DIM_X = "598a6a8f-52c0-4ba0-93af-af357411a561"
DIM_Y = "b53f9f75-04e0-4498-a7ee-c30dbb5a9011"
DIM_PRESSURE = "7307502d-f9f4-4e18-b3f2-2ce1b1a3610c"

_RASTER_OP_MASK_PEN = 9  # highlighter, same convention as ISF [inferred]


def _f32(entry) -> float | None:
    if entry is None or entry.ptype != PT_U32:
        return None
    return struct.unpack("<f", struct.pack("<I", entry.value))[0]


def _u8(obj: OneObject, prop_id: int) -> int | None:
    e = obj.props.get(prop_id)
    return int(e.value) if e is not None and isinstance(e.value, int) else None


def _utf16(data: bytes) -> str:
    return data.decode("utf-16-le", errors="replace").rstrip("\x00")


def decode_ink_path(data: bytes) -> list[int]:
    """InkPath: signed multibyte length prefix + that many signed
    multibyte delta values [verified on samples: consumes the buffer
    exactly]."""
    pos = 0
    raw, pos = read_mbuint(data, pos)
    count = raw >> 1  # length is sign-flip encoded too
    out: list[int] = []
    for _ in range(count):
        v, pos = read_mbsint(data, pos)
        out.append(v)
    return out


class _InkDimension:
    __slots__ = ("guid", "lower", "upper")

    def __init__(self, guid: str, lower: int, upper: int):
        self.guid = guid
        self.lower = lower
        self.upper = upper


def _parse_dimensions(data: bytes) -> list[_InkDimension]:
    dims = []
    for off in range(0, len(data) - 31, 32):
        guid = read_guid(data, off)
        lower, upper = struct.unpack_from("<ii", data, off + 16)
        dims.append(_InkDimension(guid, lower, upper))
    return dims


def _colorref(value: int | None) -> ir.Color:
    if value is None:
        return ir.Color(0.0, 0.0, 0.0)
    return ir.Color((value & 0xFF) / 255.0,
                    ((value >> 8) & 0xFF) / 255.0,
                    ((value >> 16) & 0xFF) / 255.0)


def _build_stroke(space: ObjectSpace, stroke_id: ExGuid,
                  base_x_pt: float, base_y_pt: float,
                  scale_x: float, scale_y: float) -> ir.Stroke | None:
    node = space.objects.get(stroke_id)
    if node is None or node.jcid != JCID_INK_STROKE:
        return None
    path_entry = node.props.get(P_INK_PATH)
    if path_entry is None or not isinstance(path_entry.value, bytes):
        return None
    values = decode_ink_path(path_entry.value)
    if not values:
        return None

    props_obj = None
    props_ref = node.ref_at(P_INK_STROKE_PROPERTIES)
    if props_ref is not None:
        props_obj = space.objects.get(props_ref)

    dims: list[_InkDimension] = []
    if props_obj is not None:
        dim_entry = props_obj.props.get(P_INK_DIMENSIONS)
        if dim_entry is not None and isinstance(dim_entry.value, bytes):
            dims = _parse_dimensions(dim_entry.value)
    if not dims:  # no descriptor: assume plain X/Y [inferred]
        dims = [_InkDimension(DIM_X, -(2 ** 31), 2 ** 31 - 1),
                _InkDimension(DIM_Y, -(2 ** 31), 2 ** 31 - 1)]

    n_dims = len(dims)
    per_dim = len(values) // n_dims
    if per_dim == 0:
        return None
    if len(values) % n_dims:
        _logger.warning("onenote: InkPath length %d not divisible by %d "
                        "dimensions; truncating", len(values), n_dims)

    guid_order = [d.guid for d in dims]
    if DIM_X not in guid_order or DIM_Y not in guid_order:
        _logger.warning("onenote: stroke without X/Y dimensions, skipped")
        return None

    def channel(guid: str) -> list[int]:
        idx = guid_order.index(guid)
        deltas = values[idx * per_dim:(idx + 1) * per_dim]
        return list(accumulate(deltas))

    xs = [base_x_pt + v * scale_x * HIMETRIC_PT for v in channel(DIM_X)]
    ys = [base_y_pt + v * scale_y * HIMETRIC_PT for v in channel(DIM_Y)]

    channels: dict[ir.Channel, list[float]] = {}
    if DIM_PRESSURE in guid_order:
        dim = dims[guid_order.index(DIM_PRESSURE)]
        span = (dim.upper - dim.lower) or 32767
        channels[ir.Channel.PRESSURE] = [
            min(1.0, max(0.0, (v - dim.lower) / span))
            for v in channel(DIM_PRESSURE)]

    color_val = None
    width_him = None
    pen_tip = raster_op = transparency = None
    if props_obj is not None:
        e = props_obj.props.get(P_INK_COLOR)
        color_val = e.value if e is not None else None
        width_him = _f32(props_obj.props.get(P_INK_WIDTH))
        if width_him is None:
            width_him = _f32(props_obj.props.get(P_INK_HEIGHT))
        pen_tip = _u8(props_obj, P_INK_PEN_TIP)
        raster_op = _u8(props_obj, P_INK_RASTER_OP)
        transparency = _u8(props_obj, P_INK_TRANSPARENCY)

    color = _colorref(color_val)
    width_pt = (width_him if width_him is not None else 53.0) * HIMETRIC_PT
    is_highlight = raster_op == _RASTER_OP_MASK_PEN
    opacity = 1.0 - (transparency or 0) / 255.0  # [inferred: ISF convention]

    return ir.Stroke(
        x=xs, y=ys,
        tool=ir.ToolRef(
            family=(ir.ToolFamily.HIGHLIGHTER if is_highlight
                    else ir.ToolFamily.PEN),
            native=ir.NativeTool(FORMAT_ID,
                                 "highlighter" if is_highlight else "pen", {
                                     "pen_tip": pen_tip,
                                     "raster_op": raster_op,
                                     "transparency": transparency,
                                     "width_himetric": width_him,
                                 }),
        ),
        color=color,
        channels=channels,
        appearance=ir.StrokeAppearance(
            mode=ir.GeometryMode.STROKED_CONSTANT,
            width=width_pt,
            color=color,
            opacity=opacity,
            cap=(ir.LineCap.SQUARE if pen_tip == 1 else ir.LineCap.ROUND),
            underlay=is_highlight,
            blend=(ir.BlendMode.DARKEN if is_highlight
                   else ir.BlendMode.NORMAL),
        ),
    )


def _collect_ink(space: ObjectSpace, container_id: ExGuid,
                 base_x_pt: float, base_y_pt: float,
                 strokes: list[ir.Stroke], visited: set[ExGuid],
                 depth: int = 0) -> None:
    """Walk one InkContainer tree (leaf strokes or nested groups)."""
    if depth > 16 or container_id in visited:
        return
    visited.add(container_id)
    container = space.objects.get(container_id)
    if container is None:
        return

    off_x = _f32(container.props.get(P_OFFSET_HORIZ)) or 0.0
    off_y = _f32(container.props.get(P_OFFSET_VERT)) or 0.0
    base_x_pt += off_x * HALF_INCH_PT
    base_y_pt += off_y * HALF_INCH_PT

    ink_data_id = container.ref_at(P_INK_DATA)
    if ink_data_id is not None:
        scale_x = _f32(container.props.get(P_INK_SCALING_X))
        scale_y = _f32(container.props.get(P_INK_SCALING_Y))
        scale_x = 1.0 if scale_x is None else scale_x
        scale_y = 1.0 if scale_y is None else scale_y
        data_node = space.objects.get(ink_data_id)
        if data_node is not None:
            for sid in data_node.ref_array_at(P_INK_STROKES) or []:
                stroke = _build_stroke(space, sid, base_x_pt, base_y_pt,
                                       scale_x, scale_y)
                if stroke is not None:
                    strokes.append(stroke)
        return

    for child_id in container.ref_array_at(P_CONTENT_CHILDREN) or []:
        _collect_ink(space, child_id, base_x_pt, base_y_pt,
                     strokes, visited, depth + 1)


def _walk_page(space: ObjectSpace, node_id: ExGuid,
               base_x_pt: float, base_y_pt: float,
               strokes: list[ir.Stroke], texts: list[ir.TextBlock],
               visited: set[ExGuid], depth: int = 0) -> None:
    """DFS the page content graph collecting ink (and best-effort text).

    Offsets on the ancestor chain accumulate; positions computed by
    OneNote's text layout engine (e.g. line positions inside outlines)
    are NOT reproduced [documented limitation]."""
    if depth > 64 or node_id in visited:
        return
    visited.add(node_id)
    obj = space.objects.get(node_id)
    if obj is None:
        return

    if obj.jcid == JCID_INK_CONTAINER:
        visited.discard(node_id)  # _collect_ink re-checks
        _collect_ink(space, node_id, base_x_pt, base_y_pt, strokes, visited)
        return

    off_x = _f32(obj.props.get(P_OFFSET_HORIZ)) or 0.0
    off_y = _f32(obj.props.get(P_OFFSET_VERT)) or 0.0
    base_x_pt += off_x * HALF_INCH_PT
    base_y_pt += off_y * HALF_INCH_PT

    if obj.jcid == JCID_RICH_TEXT:
        e = obj.props.get(P_RICH_TEXT_UNICODE)
        if e is not None and isinstance(e.value, bytes) and e.value:
            text = _utf16(e.value)
            if text:
                texts.append(ir.TextBlock(x=base_x_pt, y=base_y_pt,
                                          text=text))

    # descend through every object reference the property set carries
    for entry in obj.props.entries:
        if entry.ptype == PT_OID:
            child = obj.ref_at(entry.id)
            if child is not None:
                _walk_page(space, child, base_x_pt, base_y_pt,
                           strokes, texts, visited, depth + 1)
        elif entry.ptype == PT_OID_ARRAY:
            for child in obj.ref_array_at(entry.id) or []:
                _walk_page(space, child, base_x_pt, base_y_pt,
                           strokes, texts, visited, depth + 1)


def _find_page_node(space: ObjectSpace) -> tuple[ExGuid, OneObject] | None:
    root_id = space.roots.get(onestore.ROLE_DEFAULT_CONTENT)
    root = space.objects.get(root_id) if root_id else None
    if root is None:
        return None
    if root.jcid == JCID_PAGE_NODE:
        return root_id, root
    if root.jcid == JCID_PAGE_MANIFEST:
        for child_id in (root.ref_array_at(P_CONTENT_CHILDREN)
                         or root.ref_array_at(P_ELEMENT_CHILDREN) or []):
            child = space.objects.get(child_id)
            if child is not None and child.jcid == JCID_PAGE_NODE:
                return child_id, child
    return None


def _build_page(space: ObjectSpace, page_id: ExGuid,
                page: OneObject) -> tuple[ir.Page, str]:
    strokes: list[ir.Stroke] = []
    texts: list[ir.TextBlock] = []
    visited: set[ExGuid] = set()
    _walk_page(space, page_id, 0.0, 0.0, strokes, texts, visited)

    # ink containers not reachable from the page node (defensive; observed
    # object graphs are fully connected, but stale revisions may not be)
    for oid, obj in space.objects.items():
        if obj.jcid == JCID_INK_CONTAINER and oid not in visited:
            referenced = any(oid in o.oids for o in space.objects.values())
            if not referenced:
                _collect_ink(space, oid, 0.0, 0.0, strokes, visited)

    width = _f32(page.props.get(P_PAGE_WIDTH))
    height = _f32(page.props.get(P_PAGE_HEIGHT))
    width_pt = (width or 17.0) * HALF_INCH_PT   # default US letter
    height_pt = (height or 22.0) * HALF_INCH_PT

    x_min, y_min = 0.0, 0.0
    x_max, y_max = width_pt, height_pt
    for s in strokes:
        x_min = min(x_min, min(s.x))
        x_max = max(x_max, max(s.x))
        y_min = min(y_min, min(s.y))
        y_max = max(y_max, max(s.y))

    title = ""
    e = page.props.get(P_CACHED_TITLE)
    if e is not None and isinstance(e.value, bytes):
        title = _utf16(e.value)

    return ir.Page(
        bounds=ir.Rect(x_min, y_min, x_max, y_max),
        point_scale=1.0,  # page units are PDF points
        layers=[ir.Layer(strokes=strokes, texts=texts)],
        extra={"onenote": {"page_title": title}},
    ), title


def read_one(data: bytes, title: str = "") -> ir.Document:
    store = parse_onestore(data)
    pages: list[ir.Page] = []
    page_titles: list[str] = []
    for space in store.object_spaces:
        found = _find_page_node(space)
        if found is None:
            continue
        page, page_title = _build_page(space, *found)
        pages.append(page)
        page_titles.append(page_title)

    doc_title = title or next((t for t in page_titles if t), "")
    return ir.Document(
        format_id=FORMAT_ID,
        title=doc_title,
        pages=pages,
        metadata={
            "onestore_file_type": store.file_type,
            "object_space_count": len(store.object_spaces),
            "page_titles": page_titles,
        },
    )


class OneNoteReader:
    format_id = FORMAT_ID
    extensions = (".one",)

    def detect(self, path: Path) -> bool:
        try:
            with open(path, "rb") as f:
                head = f.read(64)
            if len(head) < 64:
                return False
            return read_guid(head, 0) in (onestore.GUID_FILE_TYPE_ONE,
                                          onestore.GUID_FILE_TYPE_TOC2)
        except (OSError, OneStoreError):
            return False

    def read(self, path: Path) -> ir.Document:
        return read_one(path.read_bytes(), title=path.stem)
