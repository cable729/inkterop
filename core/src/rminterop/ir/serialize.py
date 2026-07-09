"""IR <-> JSON.

Used for golden dumps, `rminterop inspect --json`, and `--fidelity raw`
export. Lossless for everything except Path attachments (stored as paths,
not embedded; pass embed_attachments=True to inline them base64).
"""
from __future__ import annotations

import base64
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .channels import Channel
from .model import (
    ColorBackground,
    Document,
    ImageBackground,
    Layer,
    Page,
    PdfBackground,
    RasterImage,
    Rect,
    Stroke,
    TemplateBackground,
    TextBlock,
)
from .style import BlendMode, Color, GeometryMode, LineCap, StrokeAppearance
from .tools import NativeTool, ToolFamily, ToolRef

FORMAT_VERSION = 1

_BACKGROUND_TYPES = {
    "template": TemplateBackground,
    "pdf": PdfBackground,
    "image": ImageBackground,
    "color": ColorBackground,
}


def _background_dict(bg) -> dict | None:
    if bg is None:
        return None
    for tag, cls in _BACKGROUND_TYPES.items():
        if isinstance(bg, cls):
            d = asdict(bg)
            if isinstance(bg, ImageBackground):
                d["image"]["data"] = base64.b64encode(bg.image.data).decode()
            d["type"] = tag
            return d
    raise TypeError(f"unknown background type {type(bg)!r}")


def _stroke_dict(s: Stroke) -> dict:
    d: dict[str, Any] = {
        "x": s.x,
        "y": s.y,
        "tool": {
            "family": s.tool.family.value,
            "native": asdict(s.tool.native) if s.tool.native else None,
        },
        "color": asdict(s.color),
        "channels": {ch.value: vals for ch, vals in s.channels.items()},
    }
    if s.appearance is not None:
        a = asdict(s.appearance)
        a["mode"] = s.appearance.mode.value
        a["blend"] = s.appearance.blend.value
        a["cap"] = s.appearance.cap.value
        a["join"] = s.appearance.join.value
        d["appearance"] = a
    if s.extra:
        d["extra"] = s.extra
    return d


def document_to_dict(doc: Document, embed_attachments: bool = False) -> dict:
    attachments = {}
    for key, val in doc.attachments.items():
        if isinstance(val, Path):
            if embed_attachments:
                attachments[key] = {"data": base64.b64encode(val.read_bytes()).decode()}
            else:
                attachments[key] = {"path": str(val)}
        else:
            attachments[key] = {"data": base64.b64encode(val).decode()}
    return {
        "rminterop_ir": FORMAT_VERSION,
        "format_id": doc.format_id,
        "title": doc.title,
        "orientation": doc.orientation,
        "metadata": doc.metadata,
        "extra": doc.extra,
        "attachments": attachments,
        "pages": [
            {
                "bounds": asdict(p.bounds),
                "point_scale": p.point_scale,
                "background": _background_dict(p.background),
                "extra": p.extra,
                "layers": [
                    {
                        "name": layer.name,
                        "visible": layer.visible,
                        "strokes": [_stroke_dict(s) for s in layer.strokes],
                        "texts": [asdict(t) for t in layer.texts],
                        "raster": (
                            {
                                "data": base64.b64encode(layer.raster.data).decode(),
                                "format": layer.raster.format,
                                "bounds": (
                                    asdict(layer.raster.bounds)
                                    if layer.raster.bounds
                                    else None
                                ),
                            }
                            if layer.raster
                            else None
                        ),
                    }
                    for layer in p.layers
                ],
            }
            for p in doc.pages
        ],
    }


def _color(d: dict | None) -> Color | None:
    return Color(**d) if d is not None else None


def _background_from(d: dict | None):
    if d is None:
        return None
    d = dict(d)
    tag = d.pop("type")
    if tag == "image":
        img = d["image"]
        return ImageBackground(
            RasterImage(
                data=base64.b64decode(img["data"]),
                format=img["format"],
                bounds=Rect(**img["bounds"]) if img.get("bounds") else None,
            )
        )
    if tag == "color":
        return ColorBackground(Color(**d["color"]))
    return _BACKGROUND_TYPES[tag](**d)


def _stroke_from(d: dict) -> Stroke:
    tool = ToolRef(
        family=ToolFamily(d["tool"]["family"]),
        native=NativeTool(**d["tool"]["native"]) if d["tool"].get("native") else None,
    )
    appearance = None
    if "appearance" in d:
        a = dict(d["appearance"])
        a["mode"] = GeometryMode(a["mode"])
        a["blend"] = BlendMode(a["blend"])
        a["cap"] = LineCap(a["cap"])
        a["join"] = LineCap(a["join"])
        a["color"] = Color(**a["color"])
        appearance = StrokeAppearance(**a)
    return Stroke(
        x=d["x"],
        y=d["y"],
        tool=tool,
        color=Color(**d["color"]),
        channels={Channel(k): v for k, v in d.get("channels", {}).items()},
        appearance=appearance,
        extra=d.get("extra", {}),
    )


def document_from_dict(d: dict) -> Document:
    if d.get("rminterop_ir") != FORMAT_VERSION:
        raise ValueError(f"unsupported IR version {d.get('rminterop_ir')!r}")
    attachments: dict[str, bytes | Path] = {}
    for key, val in d.get("attachments", {}).items():
        attachments[key] = (
            Path(val["path"]) if "path" in val else base64.b64decode(val["data"])
        )
    return Document(
        format_id=d["format_id"],
        title=d.get("title", ""),
        orientation=d.get("orientation", "portrait"),
        metadata=d.get("metadata", {}),
        extra=d.get("extra", {}),
        attachments=attachments,
        pages=[
            Page(
                bounds=Rect(**p["bounds"]),
                point_scale=p["point_scale"],
                background=_background_from(p.get("background")),
                extra=p.get("extra", {}),
                layers=[
                    Layer(
                        name=layer.get("name", ""),
                        visible=layer.get("visible", True),
                        strokes=[_stroke_from(s) for s in layer["strokes"]],
                        texts=[
                            TextBlock(
                                x=t["x"],
                                y=t["y"],
                                text=t["text"],
                                font_size=t.get("font_size"),
                                color=_color(t.get("color")),
                                extra=t.get("extra", {}),
                            )
                            for t in layer.get("texts", [])
                        ],
                        raster=(
                            RasterImage(
                                data=base64.b64decode(layer["raster"]["data"]),
                                format=layer["raster"]["format"],
                                bounds=(
                                    Rect(**layer["raster"]["bounds"])
                                    if layer["raster"].get("bounds")
                                    else None
                                ),
                            )
                            if layer.get("raster")
                            else None
                        ),
                    )
                    for layer in p["layers"]
                ],
            )
            for p in d["pages"]
        ],
    )


def dumps(doc: Document, indent: int | None = None, **kw) -> str:
    return json.dumps(document_to_dict(doc, **kw), indent=indent)


def loads(s: str) -> Document:
    return document_from_dict(json.loads(s))
