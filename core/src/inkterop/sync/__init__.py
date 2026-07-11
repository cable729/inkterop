"""Multi-source, multi-sink incremental sync.

The sync engine generalizes the original reMarkable->PDF mirror: `Source`s
enumerate documents from app libraries (read-only, always), `Sink`s render
them into the output tree via the format registry, `rules` filter and
customize per document, and `engine` runs incremental passes with per-source
state. The stdio JSON-RPC `daemon` drives all of it for GUI shells.
"""

from .engine import SyncEngine, sync_once  # noqa: F401
from .rules import Rules  # noqa: F401
from .sources import SyncDoc, available_sources  # noqa: F401
