"""Sync sources: read-only enumerators of app note libraries.

Every source is strictly read-only over the app's data (hard project
invariant). A source that can't be reached (app not installed, sandbox/TCC
stall, iCloud not materialized) reports unavailable instead of raising —
one bad source must never take down a sync pass or the daemon.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from .. import ir

_logger = logging.getLogger(__name__)

#: Extensions FolderSource treats as note documents. Deliberately excludes
#: ambient formats a folder may contain for other reasons (.svg, .json, .png).
NOTE_EXTENSIONS = {
    ".goodnotes", ".ntb", ".note", ".sba", ".sbn2", ".xopp", ".nebo",
    ".rm", ".rmdoc", ".one", ".sdocx", ".pkdrawing", ".tldr",
    ".excalidraw", ".uim", ".isf", ".inkz", ".inkml", ".svgz",
}

#: Wall-clock budget for container scans; sandboxed app containers can hang
#: on TCC prompts or unmaterialized iCloud stubs (observed on real machines).
SCAN_DEADLINE_S = 5.0


@dataclass
class SyncDoc:
    """One syncable document as seen by the engine (source-agnostic)."""

    source_id: str
    doc_id: str            # stable within the source (uuid or relative path)
    name: str              # display / default output name
    folder: str            # library-relative POSIX folder ("" = root)
    mtime: int             # ms epoch of last modification
    kind: str              # notebook | pdf | epub | file
    page_count: int | None = None
    path: Path | None = None   # backing file, when the source is file-based
    native: object = field(default=None, repr=False)  # source payload

    @property
    def key(self) -> str:
        return f"{self.source_id}:{self.doc_id}"


@runtime_checkable
class Source(Protocol):
    id: str
    label: str
    experimental: bool

    def available(self) -> bool: ...

    def list_documents(self) -> list[SyncDoc]: ...

    def watch_paths(self) -> list[Path]:
        """Directories whose changes should trigger a sync pass."""
        ...

    def to_ir(self, doc: SyncDoc, pen_style: str = "faithful") -> ir.Document: ...


# ---------------------------------------------------------------------------
# reMarkable desktop cache — the original mirror source, fully supported.
# ---------------------------------------------------------------------------


class RemarkableCacheSource:
    id = "remarkable"
    label = "reMarkable"
    experimental = False

    def __init__(self, cache_dir: Path | None = None):
        self._cache_dir = cache_dir
        self._lib = None

    def _library(self, reload: bool = False):
        from ..library import Library
        if self._lib is None:
            self._lib = Library(self._cache_dir)
        elif reload:
            self._lib.reload()
        return self._lib

    def available(self) -> bool:
        try:
            from ..library import default_cache_dir
            cache = self._cache_dir or default_cache_dir()
            return Path(cache).is_dir()
        except Exception:
            return False

    def list_documents(self) -> list[SyncDoc]:
        lib = self._library(reload=True)
        out = []
        for doc in lib.documents():
            folder = lib.path_of(doc).as_posix()
            out.append(SyncDoc(
                source_id=self.id,
                doc_id=doc.uuid,
                name=doc.name,
                folder="" if folder == "." else folder,
                mtime=doc.last_modified,
                kind=doc.file_type or "notebook",
                page_count=len(doc.page_uuids),
                native=doc,
            ))
        return out

    def watch_paths(self) -> list[Path]:
        return [self._library().cache_dir]

    def to_ir(self, doc: SyncDoc, pen_style: str = "faithful") -> ir.Document:
        from ..formats.remarkable.reader import read_library_document
        return read_library_document(doc.native, pen_style=pen_style)

    def render_pdf_native(self, doc: SyncDoc, out: Path, render_config) -> None:
        """The pinned mirror render path (golden-test protected)."""
        from ..render import render_notebook
        rm = doc.native
        pages = [rm.dir / f"{u}.rm" for u in rm.page_uuids]
        render_notebook(pages, out, rm.orientation == "landscape",
                        render_config, templates=rm.page_templates)


# ---------------------------------------------------------------------------
# Folder of note files — any format the registry reads; any platform.
# ---------------------------------------------------------------------------


class FolderSource:
    experimental = False

    def __init__(self, root: Path, source_id: str, label: str | None = None):
        self.root = Path(root).expanduser()
        self.id = source_id
        self.label = label or self.root.name

    def available(self) -> bool:
        return self.root.is_dir()

    def list_documents(self) -> list[SyncDoc]:
        out = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in NOTE_EXTENSIONS:
                continue
            if any(part.startswith(".") for part in
                   path.relative_to(self.root).parts):
                continue
            rel = path.relative_to(self.root)
            out.append(SyncDoc(
                source_id=self.id,
                doc_id=rel.as_posix(),
                name=path.stem,
                folder=rel.parent.as_posix() if rel.parent != Path(".") else "",
                mtime=int(path.stat().st_mtime * 1000),
                kind="file",
                path=path,
            ))
        return out

    def watch_paths(self) -> list[Path]:
        return [self.root]

    def to_ir(self, doc: SyncDoc, pen_style: str = "faithful") -> ir.Document:
        from .. import formats
        reader = formats.reader_for(doc.path)
        if reader is None:
            raise ValueError(f"no reader recognizes {doc.path}")
        return reader.read(doc.path)


# ---------------------------------------------------------------------------
# Experimental Mac app-container sources (GoodNotes / Notability).
#
# These scan the app's sandbox container for note documents. Container
# trees can HANG the calling thread (TCC mediation, iCloud stubs), so all
# filesystem walking happens in a worker thread under a deadline; a scan
# that overruns marks the source unavailable for this pass.
# ---------------------------------------------------------------------------


def _bounded_scan(roots: list[Path], suffixes: set[str],
                  deadline_s: float = SCAN_DEADLINE_S,
                  max_depth: int = 6) -> list[Path] | None:
    """rglob-with-deadline. Returns None if the deadline was exceeded."""
    found: list[Path] = []
    done = threading.Event()

    def walk() -> None:
        stop_at = time.monotonic() + deadline_s
        stack = [(r, 0) for r in roots if r.is_dir()]
        while stack:
            if time.monotonic() > stop_at:
                return  # leave `done` unset -> caller sees a timeout
            d, depth = stack.pop()
            try:
                with os.scandir(d) as it:
                    for entry in it:
                        if entry.name.startswith("."):
                            continue
                        p = Path(entry.path)
                        if entry.is_dir(follow_symlinks=False):
                            if depth < max_depth:
                                stack.append((p, depth + 1))
                        elif p.suffix.lower() in suffixes:
                            found.append(p)
            except OSError:
                continue
        done.set()

    t = threading.Thread(target=walk, daemon=True)
    t.start()
    t.join(deadline_s + 1.0)
    return found if done.is_set() else None


class _ContainerSource:
    """Shared machinery for app-container scanning sources."""

    experimental = True
    id = ""
    label = ""
    suffixes: set[str] = set()

    def _roots(self) -> list[Path]:
        raise NotImplementedError

    def available(self) -> bool:
        return any(r.is_dir() for r in self._roots())

    def _scan(self) -> list[Path]:
        files = _bounded_scan(self._roots(), self.suffixes)
        if files is None:
            _logger.warning("%s: container scan exceeded %.0fs deadline; "
                            "treating source as unavailable this pass",
                            self.id, SCAN_DEADLINE_S)
            return []
        return files

    def list_documents(self) -> list[SyncDoc]:
        out = []
        for path in sorted(self._scan()):
            try:
                st = path.stat()
            except OSError:
                continue
            out.append(SyncDoc(
                source_id=self.id,
                doc_id=path.name,
                name=path.stem,
                folder="",
                mtime=int(st.st_mtime * 1000),
                kind="file",
                path=path,
            ))
        return out

    def watch_paths(self) -> list[Path]:
        return [r for r in self._roots() if r.is_dir()]

    def to_ir(self, doc: SyncDoc, pen_style: str = "faithful") -> ir.Document:
        from .. import formats
        reader = formats.reader_for(doc.path)
        if reader is None:
            raise ValueError(f"no reader recognizes {doc.path}")
        return reader.read(doc.path)


class GoodNotesContainerSource(_ContainerSource):
    id = "goodnotes"
    label = "GoodNotes (experimental)"
    suffixes = {".goodnotes"}

    def _roots(self) -> list[Path]:
        base = Path.home() / "Library/Containers/com.goodnotesapp.x/Data"
        return [base / "Documents", base / "Library/Application Support"]


class NotabilityContainerSource(_ContainerSource):
    id = "notability"
    label = "Notability (experimental)"
    suffixes = {".note", ".ntb"}

    def _roots(self) -> list[Path]:
        # NB: Notability's library lives OUTSIDE the container Data dir
        # (see docs/HANDOFF-note-apps.md); scan both candidates.
        base = Path.home() / "Library/Containers/com.gingerlabs.Notability"
        return [base / "Data/Documents",
                base / "Data/Library/Application Support"]


# ---------------------------------------------------------------------------


def available_sources(cfg) -> list[Source]:
    """Instantiate the sources enabled in config, available or not.

    (The engine skips unavailable ones per pass; the UI shows them grayed.)
    """
    out: list[Source] = []
    if cfg.source_remarkable:
        out.append(RemarkableCacheSource(cfg.remarkable_cache_dir))
    for i, f in enumerate(cfg.source_folders):
        path = Path(f["path"]).expanduser()
        sid = f.get("id") or f"folder-{i + 1}"
        out.append(FolderSource(path, sid, f.get("name")))
    if cfg.source_goodnotes:
        out.append(GoodNotesContainerSource())
    if cfg.source_notability:
        out.append(NotabilityContainerSource())
    return out
