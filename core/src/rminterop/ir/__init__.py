"""Neutral ink IR: every format reads into / writes out of this model."""

from .channels import CHANNEL_RANGE, Channel
from .model import (
    Background,
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

__all__ = [
    "CHANNEL_RANGE",
    "Background",
    "BlendMode",
    "Channel",
    "Color",
    "ColorBackground",
    "Document",
    "GeometryMode",
    "ImageBackground",
    "Layer",
    "LineCap",
    "NativeTool",
    "Page",
    "PdfBackground",
    "RasterImage",
    "Rect",
    "Stroke",
    "StrokeAppearance",
    "TemplateBackground",
    "TextBlock",
    "ToolFamily",
    "ToolRef",
]
