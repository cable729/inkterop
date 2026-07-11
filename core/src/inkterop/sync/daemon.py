"""Stdio JSON-RPC daemon for GUI shells (the Tauri app's sidecar).

Protocol: one JSON object per line on stdin/stdout (JSON-RPC 2.0).
Requests may be handled concurrently; responses carry the request id.
Server-initiated notifications (no id) stream sync progress:

    {"jsonrpc":"2.0","method":"sync.progress","params":{"event":"doc-synced",...}}

stdout is reserved for the protocol — all logging goes to stderr. There is
deliberately no network listener: the parent process owns the pipes, so
there are no ports, tokens, or exposure to other local processes.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..config import Config
from .engine import SyncEngine
from .rules import Rules
from .sinks import EXTENSIONS

_logger = logging.getLogger(__name__)

THUMB_DIR = Path.home() / ".cache/inkterop/thumbs"
THUMB_DPI = 40  # ~450px wide for a Letter page; plenty for covers


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class Daemon:
    def __init__(self, cfg: Config | None = None, watch: bool = True,
                 debounce: float = 30.0):
        self.cfg = cfg or Config.load()
        self.engine = SyncEngine(self.cfg)
        self.watch_enabled = watch
        self.debounce = debounce
        self._out_lock = threading.Lock()
        self._stop = threading.Event()
        self._watch_thread: threading.Thread | None = None
        self._resync_lock = threading.Lock()
        self._resync_timer: threading.Timer | None = None

    # -- plumbing ---------------------------------------------------------

    def _send(self, obj: dict) -> None:
        line = json.dumps(obj, separators=(",", ":"))
        with self._out_lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    def _notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _progress(self, event: str, data: dict) -> None:
        self._notify("sync.progress", {"event": event, **data})

    def _rebuild_engine(self) -> None:
        """Config changed: reload it and re-instantiate sources."""
        self.cfg = Config.load(self.cfg.path)
        self.engine = SyncEngine(self.cfg)

    def _schedule_sync(self, delay: float = 2.0) -> None:
        """Run a sync pass shortly after a rules/config change (the file
        watcher only sees SOURCE changes, so without this a toggled
        checkbox would sit 'pending' until the library next changed)."""
        with self._resync_lock:
            if self._resync_timer is not None:
                self._resync_timer.cancel()

            def run():
                if not self.engine.paused.is_set():
                    try:
                        self.engine.sync_once(self._progress,
                                              trigger="settings")
                    except Exception:
                        _logger.exception("rules-change sync failed")

            self._resync_timer = threading.Timer(delay, run)
            self._resync_timer.daemon = True
            self._resync_timer.start()

    # -- methods ------------------------------------------------------------

    def dispatch(self, method: str, params: dict):
        handler = getattr(self, "rpc_" + method.replace(".", "_"), None)
        if handler is None:
            raise RpcError(-32601, f"method not found: {method}")
        return handler(**params)

    def rpc_ping(self):
        from .. import __version__
        return {"pong": True, "version": __version__}

    def rpc_library_list(self):
        return self.engine.snapshot()

    def rpc_status_get(self):
        try:
            return json.loads(self.engine.status_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {"state": "unknown"}

    def rpc_history_get(self):
        return self.engine.read_history()

    def rpc_sync_now(self):
        return self.engine.sync_once(self._progress, trigger="manual")

    def rpc_sync_pause(self):
        self.engine.paused.set()
        self._notify("sync.paused", {})
        return {"paused": True}

    def rpc_sync_resume(self):
        self.engine.paused.clear()
        self._notify("sync.resumed", {})
        return {"paused": False}

    def rpc_config_get(self):
        return self.cfg.to_dict()

    def rpc_config_set(self, changes: dict):
        self.cfg.update_file(changes)
        self._rebuild_engine()
        self._schedule_sync()
        return self.cfg.to_dict()

    def rpc_rules_get(self):
        return self.engine.load_rules().to_dict()

    def rpc_rules_set_mode(self, mode: str):
        rules = self.engine.load_rules()
        if mode not in ("blocklist", "allowlist"):
            raise RpcError(-32602, f"bad mode {mode!r}")
        rules.mode = mode
        rules.save(self.engine.rules_path)
        self._schedule_sync()
        return rules.to_dict()

    def rpc_rules_set_doc(self, source: str, id: str, **fields):
        rules = self.engine.load_rules()
        try:
            rules.set_doc(source, id, **fields)
        except ValueError as e:
            raise RpcError(-32602, str(e))
        rules.save(self.engine.rules_path)
        self._schedule_sync()
        return rules.to_dict()

    def rpc_rules_set_folder(self, source: str, folder: str, **fields):
        rules = self.engine.load_rules()
        try:
            rules.set_folder(source, folder, **fields)
        except ValueError as e:
            raise RpcError(-32602, str(e))
        rules.save(self.engine.rules_path)
        self._schedule_sync()
        return rules.to_dict()

    def rpc_thumbnail_get(self, key: str):
        found = self.engine.find_doc(key)
        if found is None:
            raise RpcError(-32602, f"unknown document {key!r}")
        source, doc = found
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)
        out = THUMB_DIR / f"{safe}-{doc.mtime}.png"
        if not out.exists():
            THUMB_DIR.mkdir(parents=True, exist_ok=True)
            ir_doc = source.to_ir(doc, pen_style=self.cfg.pen_style)
            ir_doc.pages = ir_doc.pages[:1]
            from ..formats.base import Fidelity
            from ..visual.png import PngWriter
            tmp = out.with_suffix(".tmp.png")
            PngWriter().write(ir_doc, tmp, Fidelity.EXACT,
                              {"dpi": THUMB_DPI})
            tmp.replace(out)
            # Drop stale thumbnails of this doc (older mtimes).
            for old in THUMB_DIR.glob(f"{safe}-*.png"):
                if old != out:
                    old.unlink(missing_ok=True)
        return {"path": str(out)}

    def rpc_formats_list(self):
        from .. import formats
        return {
            "readers": [{"id": r.format_id, "extensions": list(r.extensions)}
                        for r in formats.readers()],
            "writers": [{"id": w.format_id, "extensions": list(w.extensions),
                         "validated": w.validated}
                        for w in formats.writers()],
            "sink_formats": list(EXTENSIONS),
        }

    def rpc_convert_run(self, input: str, output: str,
                        fidelity: str = "exact", pages: str | None = None,
                        experimental: bool = False,
                        normalize: str | None = None):
        from ..convert import ConvertError, convert
        from ..formats.base import Fidelity
        options = None
        if normalize:
            from ..render.pdf import RenderConfig
            options = {"render_config": RenderConfig(normalize=normalize)}
        try:
            doc = convert(Path(input), Path(output),
                          fidelity=Fidelity(fidelity), pages=pages,
                          experimental=experimental, options=options,
                          cache_dir=self.cfg.remarkable_cache_dir)
        except ConvertError as e:
            raise RpcError(1, str(e))
        return {"output": output, "pages": len(doc.pages),
                "title": doc.title, "source_format": doc.format_id}

    # -- main loop ---------------------------------------------------------

    def _handle_line(self, line: str) -> None:
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            self._send({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": f"parse error: {e}"}})
            return
        rid = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        try:
            result = self.dispatch(method, params)
            if rid is not None:
                self._send({"jsonrpc": "2.0", "id": rid, "result": result})
        except RpcError as e:
            if rid is not None:
                self._send({"jsonrpc": "2.0", "id": rid,
                            "error": {"code": e.code, "message": str(e)}})
        except Exception as e:
            _logger.exception("rpc %s failed", method)
            if rid is not None:
                self._send({"jsonrpc": "2.0", "id": rid,
                            "error": {"code": -32603,
                                      "message": f"{type(e).__name__}: {e}"}})

    def run(self) -> int:
        if self.watch_enabled:
            self._watch_thread = threading.Thread(
                target=lambda: self.engine.watch(
                    self.debounce, self._progress, stop=self._stop),
                daemon=True, name="inkterop-watch")
            self._watch_thread.start()
        self._notify("daemon.ready", self.rpc_ping())
        with ThreadPoolExecutor(max_workers=4,
                                thread_name_prefix="rpc") as pool:
            for line in sys.stdin:
                line = line.strip()
                if line:
                    pool.submit(self._handle_line, line)
        self._stop.set()
        return 0


def main(cfg: Config | None = None, watch: bool = True,
         debounce: float = 30.0) -> int:
    return Daemon(cfg, watch=watch, debounce=debounce).run()
