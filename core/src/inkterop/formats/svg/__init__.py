"""SVG ink readers: generic SVG subset + Stylus Labs Write flavor.

Registration note: WriteReader must be registered BEFORE SvgReader —
both claim .svg and `reader_for` returns the first detect() hit.
(The .svg WRITER slot belongs to render/svg.py's SvgWriter.)
"""
from .reader import FORMAT_ID, SvgReader
from .write import WriteReader

__all__ = ["FORMAT_ID", "SvgReader", "WriteReader"]
