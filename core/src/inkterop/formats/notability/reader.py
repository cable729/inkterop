"""Notability legacy .note (zip + Session.plist) -> IR.

Session.plist is an NSKeyedArchiver archive ($archiver "GLKeyedArchiver").
Handwriting lives in objects with these parallel arrays ([verified] against
a public 2026 sample; layout first documented by jvns 2018):

  numcurves               int, N strokes
  curvespoints            float32 (x, y) pairs, all strokes concatenated
  curvesnumpoints         int32 per stroke: point count (sums to numpoints)
  curveswidth             float32 per stroke: nominal width
  curvescolors            4 bytes RGBA per stroke (alpha < 255 = translucent
                          marker/highlighter)
  curvesfractionalwidths  float32 per-point width fractions for SOME points;
                          numfractionalwidths != numpoints — the mapping to
                          strokes is [unknown], so widths stay per-stroke
                          constant and the blob is preserved in extra.

Page geometry: the continuous canvas is split using the stored document
width and native paper aspect ratio. Images are decoded from referenced
archive assets. Bare sessions without layout metadata retain the old
continuous fallback. See docs/formats/notability.md.
"""
from __future__ import annotations

import logging
import plistlib
import struct
import zipfile
from pathlib import Path, PurePosixPath

from ... import ir
from .legacy_layout import AssetLoader, geometry, images, paginate

_logger = logging.getLogger(__name__)

FORMAT_ID = "notability"

PAGE_WIDTH = 612.0  # US Letter width [inferred]; content dictates height
MARGIN = 24.0


def _resolve(objs: list, v):
    return objs[v] if isinstance(v, plistlib.UID) else v


def _handwriting_objects(archive: dict) -> list[dict]:
    objs = archive.get("$objects", [])
    return [o for o in objs
            if isinstance(o, dict) and "curvespoints" in o]


def strokes_from_handwriting(hw: dict, objs: list) -> list[ir.Stroke]:
    pts_raw = _resolve(objs, hw["curvespoints"])
    counts_raw = _resolve(objs, hw["curvesnumpoints"])
    widths_raw = _resolve(objs, hw["curveswidth"])
    colors_raw = _resolve(objs, hw["curvescolors"])

    n_curves = len(counts_raw) // 4
    counts = struct.unpack(f"<{n_curves}i", counts_raw)
    widths = struct.unpack(f"<{n_curves}f", widths_raw)
    xy = struct.unpack(f"<{len(pts_raw) // 4}f", pts_raw)

    strokes = []
    pos = 0
    for i in range(n_curves):
        n = counts[i]
        xs = [xy[2 * (pos + j)] for j in range(n)]
        ys = [xy[2 * (pos + j) + 1] for j in range(n)]
        pos += n
        r, g, b, a = colors_raw[4 * i:4 * i + 4]
        alpha = a / 255.0
        color = ir.Color(r / 255.0, g / 255.0, b / 255.0)
        family = (ir.ToolFamily.HIGHLIGHTER if alpha < 0.5
                  else ir.ToolFamily.PEN)
        strokes.append(ir.Stroke(
            x=xs, y=ys,
            tool=ir.ToolRef(
                family=family,
                native=ir.NativeTool(FORMAT_ID, "curve",
                                     {"width": widths[i], "alpha": alpha}),
            ),
            color=color,
            channels={ir.Channel.WIDTH: [widths[i]] * n},
            appearance=ir.StrokeAppearance(
                mode=ir.GeometryMode.STROKED_CONSTANT,
                width=widths[i],
                color=color,
                opacity=alpha,
                underlay=(family is ir.ToolFamily.HIGHLIGHTER),
            ),
        ))
    return strokes


def read_session(data: bytes, load_asset: AssetLoader | None = None) -> ir.Document:
    archive = plistlib.loads(data)
    if not isinstance(archive, dict) or "$objects" not in archive:
        raise ValueError("Session.plist is not an NSKeyedArchiver archive "
                         "(new cloud-era Notability format?)")
    objs = archive["$objects"]
    strokes: list[ir.Stroke] = []
    fw_blobs = []
    for hw in _handwriting_objects(archive):
        strokes.extend(strokes_from_handwriting(hw, objs))
        fw = hw.get("curvesfractionalwidths")
        if fw is not None:
            fw_blobs.append(len(_resolve(objs, fw)) // 4)

    top = archive.get("$top", {})
    root_ref = top.get("root", top.get("$0"))
    root = _resolve(objs, root_ref)
    if not isinstance(root, dict):
        root = {}
    rasters = images(objs, root, load_asset)
    geom = geometry(objs, root)
    if geom is not None:
        pages = paginate(strokes, rasters, geom)
        for page in pages:
            page.extra["notability"] = {"fractionalwidth_counts": fw_blobs}
        return ir.Document(
            format_id=FORMAT_ID, pages=pages, orientation=geom[3],
            metadata={"notability_page_width": geom[0],
                      "notability_page_height": geom[1],
                      "notability_image_objects": len(rasters)},
        )

    xs = [x for s in strokes for x in s.x]
    ys = [y for s in strokes for y in s.y]
    bounds = ir.Rect(
        min([0.0] + xs) - MARGIN,
        min([0.0] + ys) - MARGIN,
        max([PAGE_WIDTH] + xs) + MARGIN,
        max([PAGE_WIDTH * 792 / 612] + ys) + MARGIN,
    )
    page = ir.Page(
        bounds=bounds,
        point_scale=1.0,
        layers=[*rasters, ir.Layer(strokes=strokes)],
        extra={"notability": {"fractionalwidth_counts": fw_blobs}},
    )
    return ir.Document(format_id=FORMAT_ID, pages=[page])


class NotabilityReader:
    format_id = FORMAT_ID
    extensions = (".note",)

    def detect(self, path: Path) -> bool:
        # Supernote also uses .note; Notability's is a zip with Session.plist.
        if not zipfile.is_zipfile(path):
            return False
        try:
            with zipfile.ZipFile(path) as zf:
                return any(n.endswith("Session.plist") for n in zf.namelist())
        except (OSError, zipfile.BadZipFile):
            return False

    def read(self, path: Path) -> ir.Document:
        with zipfile.ZipFile(path) as zf:
            session = next(n for n in zf.namelist()
                           if n.endswith("Session.plist"))
            prefix = str(PurePosixPath(session).parent)

            def load_asset(name: str) -> bytes:
                rel = PurePosixPath(name)
                if rel.is_absolute() or ".." in rel.parts or "\\" in name:
                    raise ValueError(f"unsafe Notability asset path: {name!r}")
                member = str(PurePosixPath(prefix) / rel)
                try:
                    return zf.read(member)
                except KeyError as exc:
                    raise ValueError(f"missing Notability image: {name}") from exc

            doc = read_session(zf.read(session), load_asset=load_asset)
        doc.title = path.stem
        return doc
