"""Stylus Labs Write (.svg/.svgz) -> IR.

Write's native document format is plain SVG (styluslabs.com serves its
website pages as Write documents); `.svgz` is the same file gzipped.
The app's source (github.com/styluslabs/Write) is AGPL and was NOT
consulted: everything below comes from the styluslabs/templates README
(explicitly documents the page structure), the bytes of Write-produced
sample documents (corpus/third-party/styluslabs-write/: styluslabs.com
/svg/site1_page002.svg + /svg/features_page002.svg, template
"Dot grid 25.svg"), and the hand-made fixture.

Structure ([verified] against those samples unless marked):
- Multi-page document: root `<svg id="write-document">` containing one
  `<svg class="write-page">` per page ([verified] in the templates
  README example + template files; the site samples are single pages
  whose root itself is the page).
- Each page holds `<g class="write-content write-v3">` with page-setup
  attributes: `width`/`height` (absent on site samples -> fall back to
  the page svg's width/height), `xruling`, `yruling`, `marginLeft`,
  `papercolor` ("#RRGGBB"), `rulecolor` ("#AARRGGBB" - alpha-first;
  0x7F alpha matches the ruleline's stroke-opacity 0.498 [verified]).
- The ruling itself is drawn in `<g class="ruleline">` (with a
  `rect.pagerect` page background) - template decoration, skipped as
  content here; the write-content attrs above are the semantic copy.
- Ink: everything in write-content after the ruleline group. Observed
  pen strokes are `<path class="write-stroke-pen" fill="none"
  stroke="#RRGGBB" stroke-width="W" stroke-linecap="round"
  stroke-linejoin="round" d="M... l ...">` (absolute moveto + relative
  linetos). Ink turned into a handwritten hyperlink is wrapped in
  `<a class="hyperref">` and those paths carry NO write-stroke-* class
  ([verified]: site1_page002 has 149 classed + 27 unclassed link
  strokes) - they map to ToolFamily.UNKNOWN. `__comx`/`__comy` attrs
  look like center-of-mass bookkeeping - ignored [inferred]. Other
  `write-stroke-*` classes than "pen" have not been observed [unknown];
  unknown classes map to ToolFamily.UNKNOWN.
- Units are CSS px at 96 dpi -> point_scale 0.75 [inferred].

IR mapping: yruling>0 & xruling>0 -> TemplateBackground("grid"),
yruling only -> "lines" (pitch = yruling), xruling only -> "unknown";
papercolor/rulecolor/marginLeft kept in page.extra["write"].

The path/style/transform machinery is shared with the generic SVG
reader (sibling module).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ... import ir
from .reader import (
    Walker,
    parse_color,
    parse_length,
    read_bytes,
    sniff,
)

FORMAT_ID = "write"

#: Write stroke class suffix -> tool family. Only "pen" observed so far.
STROKE_CLASS_FAMILY = {
    "pen": ir.ToolFamily.PEN,
}

POINT_SCALE = 0.75  # CSS px -> pt [inferred]


def _classes(el: ET.Element) -> set[str]:
    return set((el.get("class") or "").split())


def _local(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _parse_write_color(text: str | None) -> ir.Color | None:
    """papercolor/rulecolor: '#RRGGBB' or alpha-first '#AARRGGBB'."""
    if not text:
        return None
    h = text.strip().lstrip("#")
    try:
        if len(h) == 8:  # AARRGGBB [verified vs ruleline stroke-opacity]
            a, r, g, b = (int(h[j:j + 2], 16) / 255.0 for j in (0, 2, 4, 6))
            return ir.Color(r, g, b, a)
        if len(h) == 6:
            return ir.Color(*(int(h[j:j + 2], 16) / 255.0 for j in (0, 2, 4)))
    except ValueError:
        return None
    return parse_color(text)


class _WriteWalker(Walker):
    """Generic walker + ruleline pruning + write-stroke-* tool classes."""

    def skip(self, el: ET.Element) -> bool:
        return "ruleline" in _classes(el)

    def tool_for(self, el: ET.Element) -> ir.ToolRef:
        cls = next((c for c in _classes(el)
                    if c.startswith("write-stroke-")), None)
        if cls is None:
            return super().tool_for(el)
        kind = cls.removeprefix("write-stroke-")
        return ir.ToolRef(
            family=STROKE_CLASS_FAMILY.get(kind, ir.ToolFamily.UNKNOWN),
            native=ir.NativeTool(FORMAT_ID, kind),
        )


def _background(content: ET.Element) -> ir.Background | None:
    def ruling(name: str) -> float:
        try:
            return float(content.get(name, "0"))
        except ValueError:
            return 0.0

    x, y = ruling("xruling"), ruling("yruling")
    rule = _parse_write_color(content.get("rulecolor"))
    gray = 0.62
    if rule is not None:
        # Luminance of the rule color blended onto white by its alpha.
        lum = 0.299 * rule.r + 0.587 * rule.g + 0.114 * rule.b
        gray = 1.0 - (1.0 - lum) * rule.a
    if x > 0 and y > 0:
        return ir.TemplateBackground(kind="grid", pitch=y, gray=gray)
    if y > 0:
        return ir.TemplateBackground(kind="lines", pitch=y, gray=gray)
    if x > 0:  # vertical-only ruling has no IR template kind
        return ir.TemplateBackground(kind="unknown", pitch=x, gray=gray)
    paper = _parse_write_color(content.get("papercolor"))
    if paper is not None and (paper.r, paper.g, paper.b) != (1.0, 1.0, 1.0):
        return ir.ColorBackground(color=paper)
    return None


def _find_content(page_el: ET.Element) -> ET.Element | None:
    for el in page_el.iter():
        if _local(el.tag) == "g" and "write-content" in _classes(el):
            return el
    return None


def _page_size(page_el: ET.Element, content: ET.Element
               ) -> tuple[float, float]:
    for source, names in ((content, ("width", "height")),
                          (page_el, ("width", "height"))):
        w = parse_length(source.get(names[0]))
        h = parse_length(source.get(names[1]))
        if w and h and w[0] > 0 and h[0] > 0:
            return w[0], h[0]
    return 768.0, 1050.0  # Write default page [inferred]


def _read_page(page_el: ET.Element) -> ir.Page:
    content = _find_content(page_el)
    walker = _WriteWalker()
    if content is not None:
        for child in content:
            walker.walk(child)
    else:  # no write-content marker on this page: take everything
        for child in page_el:
            walker.walk(child)
    w, h = _page_size(page_el, content if content is not None else page_el)
    layers = [ly for ly in walker.layers
              if ly.strokes or ly.texts] or [ir.Layer()]
    extra = {}
    if content is not None:
        extra["write"] = {
            k: content.get(k)
            for k in ("xruling", "yruling", "marginLeft",
                      "papercolor", "rulecolor")
            if content.get(k) is not None
        }
    return ir.Page(
        bounds=ir.Rect(0.0, 0.0, w, h),
        point_scale=POINT_SCALE,
        layers=layers,
        background=_background(content) if content is not None else None,
        extra=extra,
    )


def read_write_document(root: ET.Element, title: str = "") -> ir.Document:
    pages = [el for el in root.iter()
             if _local(el.tag) == "svg" and "write-page" in _classes(el)]
    if not pages:  # site samples: the root IS the page (or unmarked)
        pages = [root]
    return ir.Document(
        format_id=FORMAT_ID,
        title=title,
        pages=[_read_page(p) for p in pages],
    )


class WriteReader:
    """Stylus Labs Write SVG -> multi-page IR document."""

    format_id = FORMAT_ID
    extensions = (".svg", ".svgz")

    def detect(self, path: Path) -> bool:
        return b"write-content" in sniff(path, 8192)

    def read(self, path: Path) -> ir.Document:
        root = ET.fromstring(read_bytes(path))
        return read_write_document(root, title=Path(path).stem)
