"""Supernote .note -> IR (raster-first).

supernotelib (0.7.x) exposes each page/layer as an encoded BITMAP
(RATTA_RLE / SN_ASA_COMPRESS / PNG protocols). The device's per-stroke
vector data (the TOTALPATH block) is carried as opaque bytes and never
decoded — supernotelib's "vectorize" path is potrace tracing of the
rendered bitmap, not real pen strokes. So this reader is raster-first:
every Supernote ink layer with content becomes an ir.Layer holding a
full-page transparent-background RGBA PNG; the BGLAYER (page template)
becomes an ImageBackground. See docs/formats/supernote.md.

NOTE: render/pdf.py does not draw Layer.raster yet; raster-first docs
convert structurally but render as blank pages until that lands.
"""
from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Any

from ... import ir

_logger = logging.getLogger(__name__)

FORMAT_ID = "supernote"

# X-series: 4-byte file type then ASCII signature at offset 4.
# (Notability also uses .note but its files are PK zips — no collision.)
_X_FILETYPES = (b"note", b"mark")
_X_SIGNATURE = b"SN_FILE_VER_"
# Original (pre-X) Supernote: signature at offset 0.
_LEGACY_SIGNATURE = b"SN_FILE_ASA_"

#: Full portrait page width in PDF points (1404 px ~ 595 pt keeps an A5X
#: page near letter/A4 proportions: 1872 px -> ~793 pt tall).
POINTS_PER_PAGE_WIDTH = 595.0

_INK_LAYERS = ("MAINLAYER", "LAYER1", "LAYER2", "LAYER3")


def _png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _layer_visibility(page) -> dict[str, bool]:
    """Per-layer visibility from the LAYERINFO json (device stores ':' as
    '#'; supernotelib's get_layer_info undoes that). Missing info => all
    visible, matching the converter's MAINLAYER default."""
    info = page.get_layer_info() if page.is_layer_supported() else None
    if info is None:
        return {}
    try:
        entries = json.loads(info)
    except json.JSONDecodeError:
        import base64

        try:
            entries = json.loads(base64.b64decode(info).decode())
        except (ValueError, json.JSONDecodeError):
            _logger.warning("unparseable LAYERINFO; treating layers visible")
            return {}
    visibility: dict[str, bool] = {}
    for entry in entries:
        layer_id = entry.get("layerId")
        if entry.get("isBackgroundLayer"):
            name = "BGLAYER"
        elif layer_id == 0:
            name = "MAINLAYER"
        else:
            name = f"LAYER{layer_id}"
        visibility[name] = bool(entry.get("isVisible"))
    return visibility


def _ink_layers_bottom_up(page) -> list[str]:
    """Ink layer names with bitmap content, bottom to top.

    LAYERSEQ lists layers top-first (the converter composites it in
    reverse), so bottom-up IR order is reversed(LAYERSEQ).
    """
    with_content = {
        layer.get_name()
        for layer in page.get_layers()
        if layer.get_name() in _INK_LAYERS and layer.get_content() is not None
    }
    order = [n for n in reversed(page.get_layer_order()) if n in with_content]
    # layers present but missing from LAYERSEQ (old firmware) go on top
    order += [n for n in _INK_LAYERS if n in with_content and n not in order]
    return order


def _bg_layer_has_content(page) -> bool:
    return any(
        layer.get_name() == "BGLAYER" and layer.get_content() is not None
        for layer in page.get_layers()
    )


class SupernoteReader:
    format_id = FORMAT_ID
    extensions = (".note",)

    def detect(self, path: Path) -> bool:
        try:
            with open(path, "rb") as f:
                head = f.read(24)
        except OSError:
            return False
        if head[:4] in _X_FILETYPES and head[4:16] == _X_SIGNATURE:
            return True
        return head[:12] == _LEGACY_SIGNATURE

    def read(self, path: Path) -> ir.Document:
        import supernotelib as sn
        from supernotelib import color as sn_color
        from supernotelib.converter import (
            ImageConverter,
            VisibilityOverlay,
            build_visibility_overlay,
        )

        note = sn.load_notebook(str(path))
        width, height = note.get_width(), note.get_height()
        point_scale = POINTS_PER_PAGE_WIDTH / width
        conv = ImageConverter(note, palette=sn_color.DEFAULT_RGB_COLORPALETTE)

        kwarg_for = {"MAINLAYER": "main", "LAYER1": "layer1",
                     "LAYER2": "layer2", "LAYER3": "layer3"}

        def isolate(name: str) -> dict:
            """Overlay showing exactly one ink layer, background hidden
            (hidden BGLAYER makes the converter emit transparent RGBA)."""
            vis = {
                kwarg: (VisibilityOverlay.VISIBLE if layer_name == name
                        else VisibilityOverlay.INVISIBLE)
                for layer_name, kwarg in kwarg_for.items()
            }
            return build_visibility_overlay(
                background=VisibilityOverlay.INVISIBLE, **vis)

        pages = []
        for n in range(note.get_total_pages()):
            sp = note.get_page(n)
            horizontal = sp.get_orientation() == sp.ORIENTATION_HORIZONTAL
            w, h = (height, width) if horizontal else (width, height)
            layers: list[ir.Layer] = []
            background: ir.Background | None = None
            if sp.is_layer_supported():
                visibility = _layer_visibility(sp)
                for name in _ink_layers_bottom_up(sp):
                    img = conv.convert(n, isolate(name))
                    layers.append(ir.Layer(
                        raster=ir.RasterImage(data=_png_bytes(img),
                                              format="png"),
                        name=name,
                        visible=visibility.get(name, True),
                    ))
                if _bg_layer_has_content(sp):
                    bg_img = conv.convert(n, build_visibility_overlay(
                        background=VisibilityOverlay.VISIBLE,
                        main=VisibilityOverlay.INVISIBLE,
                        layer1=VisibilityOverlay.INVISIBLE,
                        layer2=VisibilityOverlay.INVISIBLE,
                        layer3=VisibilityOverlay.INVISIBLE,
                    ))
                    background = ir.ImageBackground(image=ir.RasterImage(
                        data=_png_bytes(bg_img), format="png"))
            elif sp.get_content() is not None:
                # pre-X, non-layered page: one flattened image
                img = conv.convert(n)
                layers.append(ir.Layer(
                    raster=ir.RasterImage(data=_png_bytes(img), format="png"),
                    name="page",
                ))
            extra: dict[str, Any] = {"supernote": {
                "page_id": sp.get_pageid(),
                "style": sp.get_style(),
                "orientation": sp.get_orientation(),
                "totalpath_bytes": len(sp.get_totalpath() or b""),
            }}
            pages.append(ir.Page(
                bounds=ir.Rect(0.0, 0.0, float(w), float(h)),
                point_scale=point_scale,
                layers=layers,
                background=background,
                extra=extra,
            ))

        orientation = "portrait"
        if pages and pages[0].bounds.width > pages[0].bounds.height:
            orientation = "landscape"
        return ir.Document(
            format_id=FORMAT_ID,
            title=path.stem,
            orientation=orientation,
            pages=pages,
            metadata={
                "signature": note.get_signature(),
                "file_type": note.get_type(),
                "file_id": note.get_fileid(),
                "device_pixels": [width, height],
            },
        )
