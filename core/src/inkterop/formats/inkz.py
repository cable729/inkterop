"""inkz — inkterop's notebook container (.inkz).

A zip wrapping a standard ink layer with the notebook concerns UIM has no
home for (multi-page structure, backgrounds, typed text, placed images,
native round-trip payloads):

```
notebook.inkz
├── manifest.json         document + page structure (see below)
├── pages/0001.uim        stroke layer, one standard UIM 3.1 file per page
├── pages/0001.overlay.json  per-stroke data UIM cannot carry (see below)
└── blobs/<sha256>        content-addressed store: attachment PDFs, images
```

Split of responsibilities:

- **UIM part** — the bulk ink data: geometry, per-point channels
  (pressure/tilt/timestamps as sensor data; resolved width/alpha as spline
  properties), stroke color, tool family (brush URI vocabulary).
  Readable by any conformant UIM consumer.
- **overlay.json** — per-stroke list, index-aligned with the UIM strokes:
  `appearance` (blend/underlay/cap/join/geometry-mode have no UIM slot),
  `tool.native` + `extra` (byte-faithful native round-trip payloads).
  THE SIZE OF THIS FILE IS THE MEASURE OF UIM'S FIT: whatever ends up
  here is what the standard couldn't express (tracked by the fitness
  matrix in docs/formats/uim.md).
- **manifest.json** — document/page structure: page bounds + point_scale,
  background oneof (template | pdf | image | color — the IR Background
  union), layer structure (strokes are stored flat per page; layers are
  reconstructed by consecutive counts), typed text, layer rasters,
  attachments. Backgrounds and rasters reference the blob store, so a
  200-page notebook with one template/PDF stores it once.

Coordinates inside UIM parts are DIPs (1/96 in) per the UIM convention;
the manifest's `point_scale` + the fixed DIP scale (0.75 pt/DIP) convert
back to source units on read. The zip is deterministic (fixed timestamps,
sorted member names).
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .. import ir
from ..ir.serialize import _background_dict, _background_from

FORMAT_VERSION = 1
DIP_TO_PT = 72.0 / 96.0  # UIM spline units are DIPs

# Appearance / native / extra ride in the overlay, index-aligned to the
# UIM strokes. Reuses the IR-JSON stroke codec for the pieces it stores.
_OVERLAY_KEYS = ("appearance", "tool_native", "extra", "color_alpha")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _appearance_dict(a: ir.StrokeAppearance | None) -> dict | None:
    if a is None:
        return None
    d = asdict(a)
    d["mode"] = a.mode.value
    d["blend"] = a.blend.value
    d["cap"] = a.cap.value
    d["join"] = a.join.value
    return d


def _appearance_from(d: dict | None) -> ir.StrokeAppearance | None:
    if d is None:
        return None
    d = dict(d)
    d["mode"] = ir.GeometryMode(d["mode"])
    d["blend"] = ir.BlendMode(d["blend"])
    d["cap"] = ir.LineCap(d["cap"])
    d["join"] = ir.LineCap(d["join"])
    d["color"] = ir.Color(**d["color"])
    return ir.StrokeAppearance(**d)


def _stroke_overlay(s: ir.Stroke) -> dict:
    d: dict[str, Any] = {}
    if s.appearance is not None:
        d["appearance"] = _appearance_dict(s.appearance)
    if s.tool.native is not None:
        d["tool_native"] = asdict(s.tool.native)
    if s.extra:
        d["extra"] = s.extra
    return d


def _apply_overlay(s: ir.Stroke, d: dict) -> None:
    if "appearance" in d:
        s.appearance = _appearance_from(d["appearance"])
    if "tool_native" in d:
        s.tool = ir.ToolRef(family=s.tool.family,
                            native=ir.NativeTool(**d["tool_native"]))
    if "extra" in d:
        s.extra = d["extra"]


def _text_dict(t: ir.TextBlock) -> dict:
    d = asdict(t)
    return d


def _text_from(d: dict) -> ir.TextBlock:
    color = ir.Color(**d["color"]) if d.get("color") else None
    return ir.TextBlock(x=d["x"], y=d["y"], text=d["text"],
                        font_size=d.get("font_size"), color=color,
                        extra=d.get("extra", {}))


class InkzWriter:
    format_id = "inkz"
    extensions = (".inkz",)
    validated = True  # our own container; round-trip covered by tests

    def write(self, doc: ir.Document, path: Path, fidelity,
              options: dict[str, Any] | None = None) -> None:
        from .uim import encode_uim

        blobs: dict[str, bytes] = {}

        def blob_ref(data: bytes) -> str:
            key = _sha(data)
            blobs[key] = data
            return key

        attachments: dict[str, dict] = {}
        for key, val in doc.attachments.items():
            data = val.read_bytes() if isinstance(val, Path) else val
            attachments[key] = {"blob": blob_ref(data)}

        parts: dict[str, bytes] = {}
        pages_manifest: list[dict] = []
        for pi, page in enumerate(doc.pages):
            n = f"{pi + 1:04d}"
            ink_name = None
            if _flat_strokes(page):
                ink_name = f"pages/{n}.uim"
                parts[ink_name] = encode_uim(doc, pi)
                overlay = [_stroke_overlay(s) for s in _flat_strokes(page)]
                if any(overlay):
                    parts[f"pages/{n}.overlay.json"] = json.dumps(
                        overlay, separators=(",", ":")).encode()

            bg = _background_dict(page.background)
            if bg and bg["type"] == "image":
                # blob-ref instead of the serializer's inline base64
                img = page.background.image
                bg = {"type": "image", "blob": blob_ref(img.data),
                      "format": img.format,
                      "bounds": asdict(img.bounds) if img.bounds else None}

            layers = []
            for layer in page.layers:
                entry: dict[str, Any] = {
                    "name": layer.name, "visible": layer.visible,
                    "n_strokes": len(layer.strokes),
                    "texts": [_text_dict(t) for t in layer.texts],
                }
                if layer.raster is not None:
                    entry["raster"] = {
                        "blob": blob_ref(layer.raster.data),
                        "format": layer.raster.format,
                        "bounds": (asdict(layer.raster.bounds)
                                   if layer.raster.bounds else None),
                    }
                layers.append(entry)

            pages_manifest.append({
                "bounds": asdict(page.bounds),
                "point_scale": page.point_scale,
                "ink": ink_name,
                "background": bg,
                "layers": layers,
                "extra": page.extra,
            })

        manifest = {
            "inkterop_inkz": FORMAT_VERSION,
            "format_id": doc.format_id,
            "title": doc.title,
            "orientation": doc.orientation,
            "metadata": doc.metadata,
            "extra": doc.extra,
            "attachments": attachments,
            "pages": pages_manifest,
        }
        parts["manifest.json"] = json.dumps(
            manifest, indent=1, sort_keys=True).encode()
        for key, data in blobs.items():
            parts[f"blobs/{key}"] = data

        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for name in sorted(parts):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, parts[name])


def _flat_strokes(page: ir.Page) -> list[ir.Stroke]:
    """ALL strokes in layer order (including invisible layers — the
    container must not drop content the way rendering does)."""
    out: list[ir.Stroke] = []
    for layer in page.layers:
        out.extend(layer.strokes)
    return out


class InkzReader:
    format_id = "inkz"
    extensions = (".inkz",)

    def detect(self, path: Path) -> bool:
        try:
            with zipfile.ZipFile(path) as z:
                if "manifest.json" not in z.namelist():
                    return False
                head = z.read("manifest.json")[:4096]
                return b"inkterop_inkz" in head
        except (OSError, zipfile.BadZipFile):
            return False

    def read(self, path: Path) -> ir.Document:
        from .uim import read_uim

        with zipfile.ZipFile(path) as z:
            manifest = json.loads(z.read("manifest.json"))
            if manifest.get("inkterop_inkz") != FORMAT_VERSION:
                raise ValueError(
                    f"unsupported inkz version {manifest.get('inkterop_inkz')!r}")

            def blob(key: str) -> bytes:
                return z.read(f"blobs/{key}")

            attachments: dict[str, bytes | Path] = {
                key: blob(entry["blob"])
                for key, entry in manifest.get("attachments", {}).items()
            }

            pages: list[ir.Page] = []
            for pm in manifest["pages"]:
                strokes: list[ir.Stroke] = []
                if pm.get("ink"):
                    uim_doc = read_uim(z.read(pm["ink"]))
                    if uim_doc.pages:
                        strokes = list(uim_doc.pages[0].strokes())

                    # UIM parts hold DIPs; convert back to source units.
                    factor = DIP_TO_PT / pm["point_scale"]
                    if abs(factor - 1.0) > 1e-9:
                        for s in strokes:
                            s.x = [v * factor for v in s.x]
                            s.y = [v * factor for v in s.y]
                            if ir.Channel.WIDTH in s.channels:
                                s.channels[ir.Channel.WIDTH] = [
                                    v * factor
                                    for v in s.channels[ir.Channel.WIDTH]]
                            if s.appearance and s.appearance.width is not None:
                                s.appearance.width *= factor

                    overlay_name = pm["ink"].replace(".uim", ".overlay.json")
                    if overlay_name in z.namelist():
                        overlay = json.loads(z.read(overlay_name))
                        for s, od in zip(strokes, overlay):
                            _apply_overlay(s, od)

                bg_dict = pm.get("background")
                if bg_dict and bg_dict.get("type") == "image":
                    background: ir.Background | None = ir.ImageBackground(
                        ir.RasterImage(
                            data=blob(bg_dict["blob"]),
                            format=bg_dict["format"],
                            bounds=(ir.Rect(**bg_dict["bounds"])
                                    if bg_dict.get("bounds") else None)))
                else:
                    background = _background_from(bg_dict)

                layers: list[ir.Layer] = []
                cursor = 0
                for lm in pm["layers"]:
                    n = lm["n_strokes"]
                    raster = None
                    if lm.get("raster"):
                        rm = lm["raster"]
                        raster = ir.RasterImage(
                            data=blob(rm["blob"]), format=rm["format"],
                            bounds=(ir.Rect(**rm["bounds"])
                                    if rm.get("bounds") else None))
                    layers.append(ir.Layer(
                        strokes=strokes[cursor:cursor + n],
                        texts=[_text_from(t) for t in lm.get("texts", [])],
                        raster=raster,
                        name=lm.get("name", ""),
                        visible=lm.get("visible", True)))
                    cursor += n

                pages.append(ir.Page(
                    bounds=ir.Rect(**pm["bounds"]),
                    point_scale=pm["point_scale"],
                    layers=layers,
                    background=background,
                    extra=pm.get("extra", {})))

        return ir.Document(
            format_id=manifest.get("format_id", "inkz"),
            title=manifest.get("title", ""),
            orientation=manifest.get("orientation", "portrait"),
            metadata=manifest.get("metadata", {}),
            extra=manifest.get("extra", {}),
            attachments=attachments,
            pages=pages)
