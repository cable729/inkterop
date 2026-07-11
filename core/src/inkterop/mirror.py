"""Back-compat wrapper for the original reMarkable->PDF mirror.

The engine itself moved to `inkterop.sync` (multi-source, multi-sink).
`mirror_once`/`watch` keep their signatures — the launchd daemon, the CLI
`mirror`/`watch` subcommands, and scripts continue to work unchanged —
but now run the sync engine restricted to the reMarkable source.
"""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .sync.engine import STATE_NAME, STATUS_PATH, SyncEngine  # noqa: F401
from .sync.sources import RemarkableCacheSource


def _engine(cfg: Config | None, cache_dir: Path | None) -> SyncEngine:
    cfg = cfg or Config.load()
    source = RemarkableCacheSource(cache_dir or cfg.remarkable_cache_dir)
    return SyncEngine(cfg, sources=[source])


def mirror_once(cfg: Config | None = None,
                cache_dir: Path | None = None) -> dict:
    """One incremental pass (reMarkable source only). Returns summary."""
    return _engine(cfg, cache_dir).sync_once()


def watch(cfg: Config | None = None, cache_dir: Path | None = None,
          debounce: float = 30.0) -> None:
    """Watch the reMarkable cache; run a pass after changes settle."""
    _engine(cfg, cache_dir).watch(debounce)
