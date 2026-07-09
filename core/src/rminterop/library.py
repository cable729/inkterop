"""Read the reMarkable desktop app's local library cache.

The desktop app mirrors the cloud library to disk in the tablet's native
xochitl layout: for each document a ``<uuid>.metadata`` (name, parent,
timestamps), a ``<uuid>.content`` (orientation, page list, file type) and a
``<uuid>/`` directory of per-page ``.rm`` v6 files. We treat that cache as a
strictly read-only source of truth.
"""

from __future__ import annotations

import json
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path

MACOS_CACHE = (
    Path.home()
    / "Library/Containers/com.remarkable.desktop/Data"
    / "Library/Application Support/remarkable/desktop"
)
WINDOWS_CACHE_CANDIDATES = [
    Path.home() / "AppData/Local/remarkable/desktop",
    Path.home() / "AppData/Roaming/remarkable/desktop",
]


def default_cache_dir() -> Path:
    if sys.platform == "darwin":
        return MACOS_CACHE
    if platform.system() == "Windows":
        for p in WINDOWS_CACHE_CANDIDATES:
            if p.is_dir():
                return p
        return WINDOWS_CACHE_CANDIDATES[0]
    raise RuntimeError(
        "No reMarkable desktop app on this platform; use an rmapi-synced "
        "directory and pass it as the cache dir."
    )


@dataclass
class Document:
    uuid: str
    name: str
    parent: str  # "" = root, "trash", or a collection uuid
    doc_type: str  # DocumentType | CollectionType
    last_modified: int  # ms epoch
    deleted: bool
    file_type: str = ""  # notebook | pdf | epub (documents only)
    orientation: str = "portrait"
    page_uuids: list[str] = field(default_factory=list)
    dir: Path | None = None

    @property
    def is_folder(self) -> bool:
        return self.doc_type == "CollectionType"


class Library:
    """The full document tree from a cache directory."""

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        if not self.cache_dir.is_dir():
            raise FileNotFoundError(
                f"reMarkable cache not found at {self.cache_dir}. "
                "Is the reMarkable desktop app installed and signed in?"
            )
        self.docs: dict[str, Document] = {}
        self.reload()

    def reload(self) -> None:
        self.docs.clear()
        for meta_path in self.cache_dir.glob("*.metadata"):
            uuid = meta_path.stem
            try:
                meta = json.loads(meta_path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if meta.get("deleted"):
                continue
            doc = Document(
                uuid=uuid,
                name=meta.get("visibleName", uuid),
                parent=meta.get("parent", ""),
                doc_type=meta.get("type", "DocumentType"),
                last_modified=int(meta.get("lastModified", 0)),
                deleted=False,
            )
            if not doc.is_folder:
                content_path = self.cache_dir / f"{uuid}.content"
                try:
                    content = json.loads(content_path.read_text())
                except (json.JSONDecodeError, OSError):
                    content = {}
                doc.file_type = content.get("fileType", "notebook")
                doc.orientation = content.get("orientation", "portrait")
                doc.page_uuids = _page_uuids(content)
                doc.dir = self.cache_dir / uuid
            self.docs[uuid] = doc

    def path_of(self, doc: Document) -> Path:
        """Folder path within the library, e.g. School/Math."""
        parts: list[str] = []
        cur = doc.parent
        seen = set()
        while cur and cur not in ("trash",) and cur in self.docs and cur not in seen:
            seen.add(cur)
            parts.append(_sanitize(self.docs[cur].name))
            cur = self.docs[cur].parent
        return Path(*reversed(parts)) if parts else Path()

    def documents(self, include_trash: bool = False) -> list[Document]:
        out = []
        for d in self.docs.values():
            if d.is_folder:
                continue
            if not include_trash and self._in_trash(d):
                continue
            out.append(d)
        return sorted(out, key=lambda d: (str(self.path_of(d)), d.name))

    def find(self, name_or_uuid: str) -> Document | None:
        if name_or_uuid in self.docs:
            return self.docs[name_or_uuid]
        matches = [
            d for d in self.documents() if d.name.lower() == name_or_uuid.lower()
        ]
        return matches[0] if matches else None

    def _in_trash(self, doc: Document) -> bool:
        cur = doc.parent
        seen = set()
        while cur and cur not in seen:
            if cur == "trash":
                return True
            seen.add(cur)
            cur = self.docs[cur].parent if cur in self.docs else ""
        return False


def _page_uuids(content: dict) -> list[str]:
    if "cPages" in content:  # 3.x format
        return [
            p["id"]
            for p in content["cPages"].get("pages", [])
            if "deleted" not in p
        ]
    return content.get("pages", [])


def _sanitize(name: str) -> str:
    return "".join("_" if c in '/\\:' else c for c in name).strip() or "_"
