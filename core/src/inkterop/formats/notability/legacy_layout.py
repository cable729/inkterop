"""Legacy .note page geometry and embedded images, in document coordinates.

Format facts and confidence are recorded in docs/formats/notability.md.
No note-app code is reused here. Image transforms are baked into bitmap
assets; handwriting remains vector ink.
"""
from __future__ import annotations

import math
import plistlib
import re
from collections.abc import Callable
from dataclasses import replace
from io import BytesIO

from PIL import Image, ImageOps

from ... import ir

AssetLoader = Callable[[str], bytes]


def resolve(objects: list, value):
    """Resolve keyed-archive containers, including the old numbered form."""
    def walk(v, seen):
        if isinstance(v, plistlib.UID):
            if v.data == 0:
                return None
            if v.data in seen:
                raise ValueError("cyclic Notability archive reference")
            return walk(objects[v.data], seen | {v.data})
        if isinstance(v, list):
            return [walk(x, seen) for x in v]
        if not isinstance(v, dict):
            return v
        if "NS.objects" in v:
            vals = walk(v["NS.objects"], seen)
            if "NS.keys" in v:
                return dict(zip(walk(v["NS.keys"], seen), vals))
            return vals
        if "NS.object.0" in v:
            n = 0
            vals = []
            while f"NS.object.{n}" in v:
                vals.append(walk(v[f"NS.object.{n}"], seen))
                n += 1
            if "NS.key.0" in v:
                return {walk(v[f"NS.key.{i}"], seen): x
                        for i, x in enumerate(vals)}
            return vals
        if "NS.bytes" in v:
            cls = v.get("$class")
            name = (objects[cls.data].get("$classname", "")
                    if isinstance(cls, plistlib.UID) else "")
            return (v["NS.bytes"].decode("utf-8") if "String" in name
                    else v["NS.bytes"])
        return {k: walk(x, seen) for k, x in v.items() if k != "$class"}
    return walk(value, set())


def geometry(objects: list, root: dict) -> tuple[float, float, int, str] | None:
    rich = resolve(objects, root.get("richText")) or {}
    if not rich:
        return None  # bare/unknown Session.plist: retain continuous fallback
    layout = resolve(objects, root.get(
        "NBNoteTakingSessionDocumentPaperLayoutModelKey")) or {}
    attrs = layout.get("documentPaperAttributes") or {}
    width = (rich.get("reflowState") or {}).get("pageWidthInDocumentCoordsKey")
    if width is None:
        match = re.match(r"lockedWidth:([\d.]+)", attrs.get("paperSizingBehavior", ""))
        width = float(match[1]) if match else 768.0
    width = float(width)
    # Native Letter in Notability is 612 x 803.25, not 612 x 792.
    paper = str(attrs.get("paperSize", "letter")).lower()
    sizes = {"letter": (612.0, 803.25), "a4": (595.28, 841.89)}
    if paper not in sizes:
        raise ValueError(f"unsupported Notability paper size: {paper!r}")
    w, h = sizes[paper]
    orientation = attrs.get("paperOrientation", "portrait")
    if orientation == "landscape":
        w, h = h, w
    if not math.isfinite(width) or width <= 0:
        raise ValueError("invalid Notability page width")
    layouts = (layout.get("pageLayoutArray") or rich.get("pageLayoutArray") or [])
    if any(e.get("kPageLayoutPDFFileNameKey") or e.get("kPageLayoutPDFFileKey")
           for e in layouts):
        raise ValueError("PDF-backed Notability page layout is not supported yet")
    # Layout arrays may be stale: their page numbers are a lower bound,
    # never permission to discard subsequent handwriting/images.
    count = max([1] + [int(e.get("kPageLayoutDocumentPageNumberKey", 0))
                       for e in layouts])
    return width, width * h / w, count, orientation


def _numbers(value, n: int) -> list[float]:
    values = [float(x) for x in re.findall(
        r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?", str(value))]
    if len(values) != n or not all(math.isfinite(x) for x in values):
        raise ValueError(f"invalid Notability geometry: {value!r}")
    return values


def _png(image: Image.Image) -> bytes:
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def images(objects: list, root: dict, load_asset: AssetLoader | None) -> list[ir.Layer]:
    rich = resolve(objects, root.get("richText")) or {}
    media = rich.get("mediaObjects") or []
    layers = []
    for item in sorted(media, key=lambda m: m.get("zIndex", 0)):
        figure = item.get("figure") or {}
        snapshot = (figure.get("FigureBackgroundObjectKey") or {}).get(
            "kImageObjectSnapshotKey")
        if not snapshot:
            continue
        if load_asset is None:
            raise ValueError("Notability images need the complete .note archive")
        name = snapshot.get("relativePath")
        if not name:
            raise ValueError("Notability image has no asset path")
        data = load_asset(name)
        x, y = _numbers(item.get("documentContentOrigin", item.get("documentOrigin")), 2)
        w, h = _numbers(item["unscaledContentSize"], 2)
        if w <= 0 or h <= 0:
            raise ValueError("invalid Notability image size")
        with Image.open(BytesIO(data)) as src:
            fmt = (src.format or "PNG").lower()
            image = src.copy()
        crop = figure.get("FigureCropRectKey")
        changed = False
        if crop:
            cx, cy, cw, ch = _numbers(crop, 4)
            if cw > 0 and ch > 0 and (cx, cy, cw, ch) != (0, 0, *image.size):
                image = image.crop((cx, cy, cx + cw, cy + ch))
                changed = True
        if item.get("isFlippedHorizontal"):
            image = ImageOps.mirror(image)
            changed = True
        if item.get("isFlippedVertical"):
            image = ImageOps.flip(image)
            changed = True
        angle = float(item.get("rotationDegrees", 0))
        if not math.isfinite(angle):
            raise ValueError("invalid Notability image rotation")
        if angle % 360:
            # Resize to display aspect before rotating, so nonuniform scales
            # do not distort the rotation. Keep the displayed center fixed.
            density = max(image.width / w, image.height / h)
            image = image.convert("RGBA").resize(
                (max(1, round(w * density)), max(1, round(h * density))),
                Image.Resampling.LANCZOS)
            image = image.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=True)
            nw, nh = image.width / density, image.height / density
            x, y = x + (w - nw) / 2, y + (h - nh) / 2
            w, h = nw, nh
            changed = True
        if changed:
            data, fmt = _png(image), "png"
        layers.append(ir.Layer(name=name, raster=ir.RasterImage(
            data=data, format=fmt, bounds=ir.Rect(x, y, x + w, y + h))))
    return layers


def _stroke_pieces(stroke: ir.Stroke, height: float):
    """Split at every crossed horizontal boundary and interpolate channels.

    Consecutive fragments on the same page stay one stroke. Original samples
    survive verbatim; only boundary samples are inserted. Dot strokes survive.
    """
    if not stroke.x:
        return
    def page(y):
        return max(0, math.floor(y / height))
    if len(stroke.x) == 1 or page(min(stroke.y)) == page(max(stroke.y)):
        yield page(stroke.y[0]), stroke
        return
    active_page = None
    xs, ys, channels = [], [], {c: [] for c in stroke.channels}
    for i in range(len(stroke.x) - 1):
        y0, y1 = stroke.y[i:i + 2]
        ts = [0.0, 1.0]
        if y1 != y0:
            lo, hi = sorted((y0, y1))
            for j in range(max(1, math.floor(lo / height) + 1), math.ceil(hi / height)):
                ts.append((j * height - y0) / (y1 - y0))
        ts.sort()
        for a, b in zip(ts, ts[1:]):
            pi = page(y0 + (y1 - y0) * (a + b) / 2)
            if active_page is not None and pi != active_page:
                yield active_page, replace(stroke, x=xs, y=ys, channels=channels)
                xs, ys, channels = [], [], {c: [] for c in stroke.channels}
            active_page = pi
            for t in ([a, b] if not xs else [b]):
                xs.append(stroke.x[i] + (stroke.x[i + 1] - stroke.x[i]) * t)
                ys.append(y0 + (y1 - y0) * t)
                for ch, values in stroke.channels.items():
                    channels[ch].append(values[i] + (values[i + 1] - values[i]) * t)
    if xs:
        yield active_page, replace(stroke, x=xs, y=ys, channels=channels)


def paginate(strokes: list[ir.Stroke], rasters: list[ir.Layer],
             geom: tuple[float, float, int, str]) -> list[ir.Page]:
    width, height, min_pages, _ = geom
    # Observed native-preview inset (15 px in a 576 px thumbnail). It keeps
    # slightly negative source x coordinates visible, with no per-page recenter.
    left = -width * 15 / 576
    pages = []
    def ensure(index):
        if index > 100_000:
            raise ValueError("unreasonable Notability page index")
        while len(pages) <= index:
            top = len(pages) * height
            pages.append(ir.Page(bounds=ir.Rect(left, top, left + width, top + height),
                                 point_scale=1.0, layers=[ir.Layer(name="Handwriting")]))
    ensure(min_pages - 1)
    for stroke in strokes:
        for pi, fragment in _stroke_pieces(stroke, height):
            ensure(pi)
            pages[pi].layers[-1].strokes.append(fragment)
    for layer in rasters:
        raster = layer.raster
        b = raster.bounds
        first, last = max(0, math.floor(b.y_min / height)), max(0, math.ceil(b.y_max / height) - 1)
        for pi in range(first, last + 1):
            ensure(pi)
            top, bottom = max(b.y_min, pi * height), min(b.y_max, (pi + 1) * height)
            if bottom <= top:
                continue
            part = raster
            if top != b.y_min or bottom != b.y_max:
                with Image.open(BytesIO(raster.data)) as image:
                    sy = (top - b.y_min) * image.height / b.height
                    ey = (bottom - b.y_min) * image.height / b.height
                    cut = image.convert("RGBA").transform(
                        (image.width, max(1, math.ceil(ey - sy))),
                        Image.Transform.EXTENT, (0, sy, image.width, ey),
                        resample=Image.Resampling.BICUBIC)
                part = ir.RasterImage(_png(cut), "png", ir.Rect(b.x_min, top, b.x_max, bottom))
            pages[pi].layers.insert(len(pages[pi].layers) - 1,
                                    replace(layer, raster=part))
    return pages
