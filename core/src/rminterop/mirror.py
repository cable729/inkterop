"""Incrementally mirror the library to a folder of PDFs.

State (per-doc lastModified at time of last successful render) lives in
<output_dir>/.rminterop-state.json. Writes are atomic (temp file + rename)
so iCloud never syncs a half-written PDF. A status file is maintained for
UI shells (menu-bar app) to display.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .config import Config
from .library import Document, Library

_logger = logging.getLogger(__name__)

STATE_NAME = ".rminterop-state.json"
STATUS_PATH = Path.home() / ".config/rminterop/status.json"


def _wanted(lib: Library, doc: Document, cfg: Config) -> bool:
    if doc.file_type == "notebook" and not cfg.notebooks:
        return False
    if doc.file_type == "pdf" and not cfg.pdfs:
        return False
    if doc.file_type == "epub" and not cfg.epubs:
        return False
    folder = str(lib.path_of(doc))
    return not any(folder == ex or folder.startswith(ex + "/") for ex in cfg.exclude)


def _write_status(**kw) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"time": int(time.time()), **kw}))
    tmp.replace(STATUS_PATH)


def mirror_once(cfg: Config | None = None, cache_dir: Path | None = None) -> dict:
    """One incremental pass. Returns summary counts."""
    from .render import render_notebook  # deferred: reportlab import is slow

    cfg = cfg or Config.load()
    lib = Library(cache_dir)
    out_root = cfg.output_dir
    out_root.mkdir(parents=True, exist_ok=True)
    state_path = out_root / STATE_NAME
    try:
        state = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError):
        state = {}

    rendered = skipped = failed = 0
    live_outputs = set()
    t0 = time.time()
    for doc in lib.documents():
        if not _wanted(lib, doc, cfg):
            continue
        rel = lib.path_of(doc) / f"{doc.name}.pdf"
        live_outputs.add(str(rel))
        out_pdf = out_root / rel
        if state.get(doc.uuid) == doc.last_modified and out_pdf.exists():
            skipped += 1
            continue
        pages = [doc.dir / f"{u}.rm" for u in doc.page_uuids]
        tmp = out_pdf.with_name(out_pdf.name + ".rminterop-tmp")
        try:
            render_notebook(pages, tmp, doc.orientation == "landscape",
                            cfg.render_config())
            tmp.replace(out_pdf)
            state[doc.uuid] = doc.last_modified
            rendered += 1
            _logger.info("rendered %s", rel)
        except Exception:
            failed += 1
            tmp.unlink(missing_ok=True)
            _logger.warning("failed to render %s", rel, exc_info=True)

    # Remove mirrored PDFs whose source is gone (moved/deleted/renamed).
    removed = 0
    for pdf in out_root.rglob("*.pdf"):
        rel = pdf.relative_to(out_root)
        if str(rel) not in live_outputs:
            pdf.unlink()
            removed += 1
    for d in sorted((p for p in out_root.rglob("*") if p.is_dir()), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()

    tmp_state = state_path.with_suffix(".tmp")
    tmp_state.write_text(json.dumps(state))
    tmp_state.replace(state_path)

    summary = {"rendered": rendered, "skipped": skipped, "failed": failed,
               "removed": removed, "seconds": round(time.time() - t0, 1),
               "documents": len(live_outputs)}
    _write_status(**summary)
    return summary


def watch(cfg: Config | None = None, cache_dir: Path | None = None,
          debounce: float = 30.0) -> None:
    """Watch the cache dir; run mirror_once after changes settle."""
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    from .library import default_cache_dir

    cfg = cfg or Config.load()
    cache = Path(cache_dir) if cache_dir else default_cache_dir()
    pending = {"t": 0.0}

    class Handler(FileSystemEventHandler):
        def on_any_event(self, event):
            if event.is_directory or STATE_NAME in str(event.src_path):
                return
            pending["t"] = time.time()

    _logger.info("initial pass")
    mirror_once(cfg, cache)
    obs = Observer()
    obs.schedule(Handler(), str(cache), recursive=True)
    obs.start()
    _logger.info("watching %s (debounce %.0fs)", cache, debounce)
    try:
        while True:
            time.sleep(2)
            if pending["t"] and time.time() - pending["t"] > debounce:
                pending["t"] = 0.0
                summary = mirror_once(cfg, cache)
                _logger.info("mirror pass: %s", summary)
    except KeyboardInterrupt:
        pass
    finally:
        obs.stop()
        obs.join()
