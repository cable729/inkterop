"""Format registry: one reader (native -> IR) / writer (IR -> native) each.

Plain module-level registration by explicit import — no plugin magic.
"""
from __future__ import annotations

from pathlib import Path

from .base import Fidelity, FormatReader, FormatWriter  # noqa: F401

_READERS: list[FormatReader] = []
_WRITERS: list[FormatWriter] = []
_LOADED = False


def _load() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    from ..render.pdf import PdfWriter
    from ..render.svg import SvgWriter
    from .goodnotes import GoodnotesReader
    from .inkml import InkmlReader, InkmlWriter
    from .irjson import IrJsonReader, IrJsonWriter
    from .nebo import NeboReader
    from .notability import NotabilityReader, NtbReader
    from .remarkable.reader import RemarkableReader
    from .saber import SaberReader, SaberWriter
    from .supernote import SupernoteReader
    from .xopp import XoppReader, XoppWriter

    _READERS.extend([
        RemarkableReader(), IrJsonReader(), XoppReader(), InkmlReader(),
        GoodnotesReader(), SupernoteReader(), NotabilityReader(),
        NtbReader(), SaberReader(), NeboReader(),
    ])
    _WRITERS.extend([
        PdfWriter(), IrJsonWriter(), XoppWriter(), InkmlWriter(), SvgWriter(),
        SaberWriter(),
    ])


def readers() -> list[FormatReader]:
    _load()
    return list(_READERS)


def writers() -> list[FormatWriter]:
    _load()
    return list(_WRITERS)


def register_reader(reader: FormatReader) -> None:
    _load()
    _READERS.append(reader)


def register_writer(writer: FormatWriter) -> None:
    _load()
    _WRITERS.append(writer)


def reader_for(path: Path) -> FormatReader | None:
    """Pick a reader: extension match first, confirmed by detect()."""
    _load()
    ext = path.suffix.lower()
    candidates = [r for r in _READERS if ext in r.extensions]
    for r in candidates:
        if r.detect(path):
            return r
    # Extension is ambiguous across apps (.note!); fall back to sniffing all.
    for r in _READERS:
        if ext not in r.extensions and r.detect(path):
            return r
    return None


def writer_for(path: Path) -> FormatWriter | None:
    _load()
    ext = path.suffix.lower()
    for w in _WRITERS:
        if ext in w.extensions:
            return w
    return None
