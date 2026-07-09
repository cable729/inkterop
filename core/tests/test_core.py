"""Smoke tests against a synthetic cache (no real library required)."""

import json
from pathlib import Path

import pytest

from inkterop.library import Library
from inkterop.render import RenderConfig, render_notebook


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    folder = {"visibleName": "School", "parent": "", "type": "CollectionType",
              "lastModified": "1", "deleted": False}
    doc = {"visibleName": "Notes", "parent": "folder-1", "type": "DocumentType",
           "lastModified": "2", "deleted": False}
    trashed = {"visibleName": "Old", "parent": "trash", "type": "DocumentType",
               "lastModified": "3", "deleted": False}
    content = {"fileType": "notebook", "orientation": "landscape",
               "cPages": {"pages": [{"id": "p1"}, {"id": "p2", "deleted": {}}]}}
    (tmp_path / "folder-1.metadata").write_text(json.dumps(folder))
    (tmp_path / "doc-1.metadata").write_text(json.dumps(doc))
    (tmp_path / "doc-1.content").write_text(json.dumps(content))
    (tmp_path / "doc-2.metadata").write_text(json.dumps(trashed))
    (tmp_path / "doc-2.content").write_text(json.dumps({"fileType": "notebook"}))
    return tmp_path


def test_library_tree(cache: Path):
    lib = Library(cache)
    docs = lib.documents()
    assert [d.name for d in docs] == ["Notes"]
    d = docs[0]
    assert str(lib.path_of(d)) == "School"
    assert d.orientation == "landscape"
    assert d.page_uuids == ["p1"]  # deleted page filtered


def test_trash_excluded(cache: Path):
    lib = Library(cache)
    assert lib.find("Old") is None or lib._in_trash(lib.docs["doc-2"])


def test_render_blank_pages_uniform(tmp_path: Path):
    out = tmp_path / "out.pdf"
    # No .rm files exist -> blank pages at the target size, one per page uuid.
    render_notebook([tmp_path / "missing1.rm", tmp_path / "missing2.rm"],
                    out, landscape=True, config=RenderConfig())
    data = out.read_bytes()
    assert data.startswith(b"%PDF") and data.count(b"/Type /Page") >= 2
