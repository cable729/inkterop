"""The neutral ink document model.

Every format converts through this: reader (native -> Document) on one
side, writer/renderer (Document -> native/PDF/SVG/...) on the other.

Coordinates stay in SOURCE units with a declared `Page.point_scale`
(units -> PDF points); writers scale as needed. Y grows downward.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .channels import Channel
from .style import Color, StrokeAppearance
from .tools import ToolRef


@dataclass
class Rect:
    """Axis-aligned bounding box in source units."""
    x_min: float
    y_min: float
    x_max: float
    y_max: float

    @property
    def width(self) -> float:
        return self.x_max - self.x_min

    @property
    def height(self) -> float:
        return self.y_max - self.y_min


@dataclass
class Stroke:
    """One pen stroke: parallel x/y point arrays plus optional per-point
    channels (pressure, tilt, width...), a semantic tool reference, and an
    optional exact appearance override. See the IR spec for the
    three-fidelity model."""
    x: list[float]
    y: list[float]
    tool: ToolRef
    color: Color  # semantic/base color
    channels: dict[Channel, list[float]] = field(default_factory=dict)
    appearance: StrokeAppearance | None = None  # None => style from tool family
    extra: dict[str, Any] = field(default_factory=dict)  # namespaced by format id

    def __len__(self) -> int:
        return len(self.x)

    def validate(self) -> None:
        if len(self.x) != len(self.y):
            raise ValueError(f"x/y length mismatch: {len(self.x)} != {len(self.y)}")
        for ch, values in self.channels.items():
            if len(values) != len(self.x):
                raise ValueError(
                    f"channel {ch.value} length {len(values)} != {len(self.x)} points"
                )


@dataclass
class TextBlock:
    """A typed-text run anchored at (x, y) in source units."""
    x: float
    y: float
    text: str
    font_size: float | None = None
    color: Color | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RasterImage:
    """Bitmap layer content (e.g. Supernote RLE layers, embedded images)."""

    data: bytes  # encoded image bytes
    format: str  # "png", "jpeg", ...
    bounds: Rect | None = None  # placement in page units; None => full page


@dataclass
class Layer:
    """One z-ordered layer of a page: vector strokes, text, and/or a
    raster image."""
    strokes: list[Stroke] = field(default_factory=list)
    texts: list[TextBlock] = field(default_factory=list)
    raster: RasterImage | None = None
    name: str = ""
    visible: bool = True


# --- page backgrounds -------------------------------------------------------

@dataclass
class TemplateBackground:
    """Procedural page template, resolved to concrete params by the reader."""

    kind: str  # "dots" | "lines" | "grid" | "unknown"
    name: str = ""  # source template name, e.g. "P Dots S"
    pitch: float = 0.0  # dot/line spacing, source units
    line_width: float = 0.0
    dot_radius: float = 0.0
    gray: float = 0.62  # 0-1 luminance


@dataclass
class PdfBackground:
    """A page of an attached PDF used as the page background."""
    attachment_key: str  # key into Document.attachments
    page_index: int


@dataclass
class ImageBackground:
    """A raster image used as the page background."""
    image: RasterImage


@dataclass
class ColorBackground:
    """A solid-color page background."""
    color: Color


Background = TemplateBackground | PdfBackground | ImageBackground | ColorBackground


@dataclass
class Page:
    """One page: bounds + unit scale, layers bottom-to-top, and an
    optional background."""
    bounds: Rect  # in source units (rM: x centered on 0, grown y)
    point_scale: float  # source units -> PDF points
    layers: list[Layer] = field(default_factory=list)
    background: Background | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def strokes(self):
        for layer in self.layers:
            if layer.visible:
                yield from layer.strokes


@dataclass
class Document:
    """The root IR object a reader produces and a writer consumes."""
    format_id: str
    title: str = ""
    pages: list[Page] = field(default_factory=list)
    orientation: str = "portrait"  # portrait | landscape (hint)
    attachments: dict[str, bytes | Path] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        for pi, page in enumerate(self.pages):
            for si_, stroke in enumerate(page.strokes()):
                try:
                    stroke.validate()
                except ValueError as e:
                    raise ValueError(f"page {pi} stroke {si_}: {e}") from e
