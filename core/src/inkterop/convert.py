"""Cross-format conversion orchestration: read -> IR -> write."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import formats, ir
from .formats.base import Fidelity

_logger = logging.getLogger(__name__)


class ConvertError(Exception):
    pass


def _forbidden_roots() -> list[Path]:
    """Directories we must never write into (source-of-truth caches)."""
    roots = []
    try:
        from .library import default_cache_dir
        roots.append(default_cache_dir())
    except Exception:
        pass
    # Note apps' own containers (their internal stores sync to their
    # clouds; a bad write propagates before anyone notices) and the
    # mirror's iCloud output dir (owned by the mirror engine).
    home = Path.home()
    roots += [
        home / "Library" / "Containers" / "com.goodnotesapp.x",
        home / "Library" / "Containers" / "com.gingerlabs.Notability",
        home / "Library" / "Containers" / "com.adilhanney.saber",
        home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
             / "reMarkable",
    ]
    return roots


def parse_pages(spec: str, n: int) -> list[int]:
    """'1-3,7' -> zero-based page indices (bounds-checked)."""
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a) - 1, int(b)))
        else:
            out.append(int(part) - 1)
    bad = [i + 1 for i in out if i < 0 or i >= n]
    if bad:
        raise ConvertError(f"page(s) {bad} out of range (document has {n})")
    return out


def read_input(in_path: Path, cache_dir: Path | None = None,
               pen_style: str = "faithful") -> ir.Document:
    """Read a file via the registry, or a library document by name/uuid."""
    if in_path.exists() and in_path.is_file():
        reader = formats.reader_for(in_path)
        if reader is None:
            raise ConvertError(
                f"no reader recognizes {in_path.name} "
                f"(known: {sorted({e for r in formats.readers() for e in r.extensions})})"
            )
        _logger.info("reading %s as %s", in_path.name, reader.format_id)
        return reader.read(in_path)

    # Not a file: try the reMarkable library by visible name or uuid.
    from .formats.remarkable.reader import read_library_document
    from .library import Library

    lib = Library(cache_dir)
    doc = lib.find(str(in_path))
    if doc is None or doc.is_folder:
        raise ConvertError(f"not a file and not a library document: {in_path}")
    return read_library_document(doc, pen_style=pen_style)


def convert(in_path: Path, out_path: Path,
            fidelity: Fidelity = Fidelity.EXACT,
            pages: str | None = None,
            experimental: bool = False,
            force: bool = False,
            cache_dir: Path | None = None,
            options: dict[str, Any] | None = None) -> ir.Document:
    """Convert one document; returns the IR that was written."""
    writer = formats.writer_for(out_path)
    if writer is None:
        raise ConvertError(
            f"no writer for {out_path.suffix!r} "
            f"(known: {sorted({e for w in formats.writers() for e in w.extensions})})"
        )
    if not writer.validated and not experimental:
        raise ConvertError(
            f"the {writer.format_id} writer is not validated against the "
            f"target app yet; pass --experimental to use it anyway"
        )
    out_resolved = out_path.resolve()
    for root in _forbidden_roots():
        if root and out_resolved.is_relative_to(root) and not force:
            raise ConvertError(
                f"refusing to write into source-of-truth dir {root}"
            )

    doc = read_input(in_path, cache_dir=cache_dir)
    if pages:
        idx = parse_pages(pages, len(doc.pages))
        doc.pages = [doc.pages[i] for i in idx]
    doc.validate()
    writer.write(doc, out_path, fidelity, options)
    return doc
