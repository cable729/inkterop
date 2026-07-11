"""Incremental multi-source sync engine.

Generalizes the original reMarkable->PDF mirror (mirror.py, now a wrapper
around this): enumerate documents from every enabled source, apply rules
(allow/block, rename, folder, per-doc format), render changed docs through
the chosen sink, and clean up outputs whose document disappeared or whose
rules changed.

State lives in <output_dir>/.inkterop-state.json:
    v2: {"version": 2, "docs": {"<source>:<id>": {"mtime": ms,
         "outputs": ["rel/path.pdf", ...]}}}
    v1 (legacy mirror): {"<uuid>": mtime} — migrated on first pass by
    treating a matching mtime + existing expected PDF as already synced.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from ..config import Config
from . import sinks
from .rules import Rules
from .sources import Source, SyncDoc, available_sources

_logger = logging.getLogger(__name__)

STATE_NAME = ".inkterop-state.json"
STATUS_PATH = Path.home() / ".config/inkterop/status.json"

ProgressFn = Callable[[str, dict], None]


def _load_state(state_path: Path) -> dict:
    try:
        raw = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"version": 2, "docs": {}}
    if isinstance(raw, dict) and raw.get("version") == 2:
        raw.setdefault("docs", {})
        return raw
    # v1 legacy: {uuid: mtime} from the pre-sync mirror.
    return {"version": 2, "docs": {}, "_legacy": raw if isinstance(raw, dict) else {}}


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


class SyncEngine:
    def __init__(self, cfg: Config | None = None,
                 sources: list[Source] | None = None,
                 rules_path: Path | None = None):
        self.cfg = cfg or Config.load()
        self.sources = sources if sources is not None \
            else available_sources(self.cfg)
        # rules.toml and status.json live next to the config file in use,
        # so a --config override isolates ALL engine state (tests, sidecar).
        base = self.cfg.path.parent if self.cfg.path else STATUS_PATH.parent
        self.rules_path = rules_path or base / "rules.toml"
        self.status_path = base / "status.json"
        self._lock = threading.Lock()  # one sync pass at a time
        self.paused = threading.Event()  # set => watcher skips passes

    def write_status(self, **kw) -> None:
        _write_json_atomic(self.status_path, {"time": int(time.time()), **kw})

    # -- rules / filtering -------------------------------------------------

    def load_rules(self) -> Rules:
        return Rules.load(self.rules_path)

    def _wanted(self, doc: SyncDoc, rules: Rules) -> bool:
        cfg = self.cfg
        if doc.kind == "notebook" and not cfg.notebooks:
            return False
        if doc.kind == "pdf" and not cfg.pdfs:
            return False
        if doc.kind == "epub" and not cfg.epubs:
            return False
        if doc.source_id == "remarkable":
            # Legacy config.toml folder excludes, kept working.
            if any(doc.folder == ex or doc.folder.startswith(ex + "/")
                   for ex in cfg.exclude):
                return False
        return rules.wanted(doc.source_id, doc.doc_id, doc.folder)

    def _plan(self, doc: SyncDoc, rules: Rules) -> tuple[Path, str, str]:
        """(relative output dir, output name, sink format) after overrides."""
        rule = rules.rule_for(doc.source_id, doc.doc_id)
        folder = rule.folder if rule.folder is not None else doc.folder
        name = rule.name or doc.name
        fmt = rule.format or self.cfg.default_format
        rel_dir = Path(*[_sanitize(p) for p in
                         Path(folder).parts]) if folder else Path()
        return rel_dir, _sanitize_name(name), fmt

    # -- the pass ------------------------------------------------------------

    def sync_once(self, progress: ProgressFn | None = None) -> dict:
        with self._lock:
            return self._sync_once_locked(progress)

    def _sync_once_locked(self, progress: ProgressFn | None) -> dict:
        def emit(event: str, **data) -> None:
            if progress:
                try:
                    progress(event, data)
                except Exception:
                    _logger.exception("progress callback failed")

        cfg = self.cfg
        rules = self.load_rules()
        out_root = cfg.output_dir
        out_root.mkdir(parents=True, exist_ok=True)
        state_path = out_root / STATE_NAME
        state = _load_state(state_path)
        legacy: dict = state.pop("_legacy", {})

        rendered = skipped = failed = 0
        failures: list[dict] = []
        live_keys: set[str] = set()
        live_outputs: set[str] = set()
        t0 = time.time()
        self.write_status(state="syncing")
        emit("pass-started")

        for source in self.sources:
            try:
                if not source.available():
                    emit("source-unavailable", source=source.id)
                    continue
                docs = source.list_documents()
            except Exception as e:
                _logger.warning("source %s failed to list: %s", source.id, e,
                                exc_info=True)
                emit("source-error", source=source.id, error=str(e))
                continue

            for doc in docs:
                if not self._wanted(doc, rules):
                    continue
                rel_dir, name, fmt = self._plan(doc, rules)
                key = doc.key
                live_keys.add(key)
                entry = state["docs"].get(key)

                if entry is None and doc.source_id == "remarkable" \
                        and doc.doc_id in legacy:
                    # v1 mirror state: trust it if the old-shape PDF exists.
                    legacy_pdf = out_root / rel_dir / f"{name}.pdf"
                    if legacy[doc.doc_id] == doc.mtime and legacy_pdf.exists():
                        entry = {"mtime": doc.mtime,
                                 "outputs": [str((rel_dir / f"{name}.pdf"))]}
                        state["docs"][key] = entry

                # Unchanged only if mtime matches AND the recorded outputs
                # still match the current plan (rename/move/format changes
                # force a re-render) AND every file is still on disk.
                expected = str(rel_dir / f"{name}{sinks.EXTENSIONS[fmt]}")
                if (entry and entry.get("mtime") == doc.mtime
                        and expected in entry.get("outputs", [])
                        and all((out_root / o).exists()
                                for o in entry["outputs"])):
                    skipped += 1
                    live_outputs.update(entry["outputs"])
                    continue

                emit("doc-started", key=key, name=doc.name, format=fmt)
                try:
                    written = sinks.write_doc(
                        fmt, source, doc, out_root / rel_dir, name,
                        render_config=cfg.render_config(),
                        pen_style=cfg.pen_style)
                    rels = [str(p.relative_to(out_root)) for p in written]
                    # Outputs that moved/renamed/changed format: drop stale.
                    if entry:
                        for old in entry.get("outputs", []):
                            if old not in rels:
                                (out_root / old).unlink(missing_ok=True)
                    state["docs"][key] = {"mtime": doc.mtime, "outputs": rels}
                    live_outputs.update(rels)
                    rendered += 1
                    _logger.info("synced %s -> %s", key, rels[0])
                    emit("doc-synced", key=key, name=doc.name, outputs=rels)
                except Exception as e:
                    failed += 1
                    failures.append({"key": key, "name": doc.name,
                                     "error": str(e)})
                    _logger.warning("failed to sync %s", key, exc_info=True)
                    emit("doc-failed", key=key, name=doc.name, error=str(e))
                    # Keep previous outputs (if any) rather than deleting a
                    # good older render because a new one failed.
                    if entry:
                        live_outputs.update(entry.get("outputs", []))
                        state["docs"][key] = entry

        # Remove outputs of docs that are gone, blocked, or out of scope.
        removed = 0
        for key in list(state["docs"]):
            if key in live_keys:
                continue
            for old in state["docs"][key].get("outputs", []):
                p = out_root / old
                if p.exists():
                    p.unlink()
                    removed += 1
            del state["docs"][key]
        # Legacy v1 cleanup: PDFs the old mirror tracked only by rglob.
        if legacy:
            for pdf in out_root.rglob("*.pdf"):
                rel = str(pdf.relative_to(out_root))
                if rel not in live_outputs:
                    pdf.unlink()
                    removed += 1
        for d in sorted((p for p in out_root.rglob("*") if p.is_dir()),
                        reverse=True):
            if not any(d.iterdir()):
                d.rmdir()

        _write_json_atomic(state_path, state)
        summary = {"rendered": rendered, "skipped": skipped, "failed": failed,
                   "removed": removed, "seconds": round(time.time() - t0, 1),
                   "documents": len(live_keys)}
        self.write_status(state="idle", failures=failures, **summary)
        emit("pass-finished", **summary)
        return summary

    # -- watching --------------------------------------------------------

    def watch(self, debounce: float = 30.0,
              progress: ProgressFn | None = None,
              stop: threading.Event | None = None) -> None:
        """Blocking watch loop over every available source's paths."""
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        stop = stop or threading.Event()
        pending = {"t": 0.0}

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                if event.is_directory or STATE_NAME in str(event.src_path):
                    return
                pending["t"] = time.time()

        _logger.info("initial pass")
        self.sync_once(progress)
        obs = Observer()
        n_watched = 0
        for source in self.sources:
            try:
                if not source.available():
                    continue
                for path in source.watch_paths():
                    obs.schedule(Handler(), str(path), recursive=True)
                    n_watched += 1
            except Exception:
                _logger.warning("cannot watch %s", source.id, exc_info=True)
        obs.start()
        _logger.info("watching %d paths (debounce %.0fs)", n_watched, debounce)
        try:
            while not stop.is_set():
                time.sleep(2)
                if self.paused.is_set():
                    continue
                if pending["t"] and time.time() - pending["t"] > debounce:
                    pending["t"] = 0.0
                    summary = self.sync_once(progress)
                    _logger.info("sync pass: %s", summary)
        except KeyboardInterrupt:
            pass
        finally:
            obs.stop()
            obs.join()

    # -- introspection (daemon / UI) ---------------------------------------

    def snapshot(self) -> dict:
        """Sources + documents + per-doc effective plan and sync state."""
        rules = self.load_rules()
        out_root = self.cfg.output_dir
        state = _load_state(out_root / STATE_NAME)
        src_infos, docs = [], []
        for source in self.sources:
            try:
                ok = source.available()
            except Exception:
                ok = False
            src_infos.append({"id": source.id, "label": source.label,
                              "available": ok,
                              "experimental": source.experimental})
            if not ok:
                continue
            try:
                listed = source.list_documents()
            except Exception as e:
                _logger.warning("source %s list failed: %s", source.id, e)
                continue
            for doc in listed:
                rel_dir, name, fmt = self._plan(doc, rules)
                entry = state["docs"].get(doc.key)
                wanted = self._wanted(doc, rules)
                expected = str(rel_dir / f"{name}{sinks.EXTENSIONS[fmt]}")
                if not wanted:
                    sync_state = "blocked"
                elif (entry and entry.get("mtime") == doc.mtime
                        and expected in entry.get("outputs", [])
                        and all((out_root / o).exists()
                                for o in entry.get("outputs", []))):
                    sync_state = "synced"
                else:
                    sync_state = "pending"
                rule = rules.rule_for(doc.source_id, doc.doc_id)
                docs.append({
                    "key": doc.key, "source": doc.source_id,
                    "id": doc.doc_id, "name": doc.name,
                    "folder": doc.folder, "mtime": doc.mtime,
                    "kind": doc.kind, "pages": doc.page_count,
                    "state": sync_state, "format": fmt,
                    "output": str(rel_dir / f"{name}{sinks.EXTENSIONS[fmt]}"),
                    "outputs": (entry or {}).get("outputs", []),
                    "rule": rule.to_dict(),
                })
        return {"sources": src_infos, "docs": docs,
                "output_dir": str(out_root), "mode": rules.mode}

    def find_doc(self, key: str) -> tuple[Source, SyncDoc] | None:
        source_id, _, doc_id = key.partition(":")
        for source in self.sources:
            if source.id != source_id:
                continue
            try:
                for doc in source.list_documents():
                    if doc.doc_id == doc_id:
                        return source, doc
            except Exception:
                return None
        return None


def sync_once(cfg: Config | None = None,
              progress: ProgressFn | None = None) -> dict:
    """One incremental pass over all configured sources."""
    return SyncEngine(cfg).sync_once(progress)


def _sanitize(name: str) -> str:
    """Folder-component sanitize — matches library.path_of's convention."""
    return "".join("_" if c in '/\\:' else c for c in name).strip() or "_"


#: Filename sanitize is looser than the folder one: the original mirror
#: wrote doc names verbatim (":" is legal on APFS/ext4 and present in real
#: libraries), so only genuinely path-hazardous characters are replaced.
_BAD_NAME_CHARS = '/\\' if sys.platform != "win32" else '/\\:<>"|?*'


def _sanitize_name(name: str) -> str:
    return "".join("_" if c in _BAD_NAME_CHARS else c
                   for c in name).strip() or "_"
