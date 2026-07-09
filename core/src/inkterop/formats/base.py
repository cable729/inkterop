"""Reader/writer protocols and conversion fidelity levels."""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .. import ir


class Fidelity(str, Enum):
    EXACT = "exact"  # reproduce the SOURCE app's rendering (appearance)
    NATIVE = "native"  # map tools semantically; target restyles them
    RAW = "raw"  # the per-point event data itself (channels)


@runtime_checkable
class FormatReader(Protocol):
    format_id: str
    extensions: tuple[str, ...]

    def detect(self, path: Path) -> bool:
        """Cheap magic-byte/structure sniff; extension already matched."""
        ...

    def read(self, path: Path) -> ir.Document: ...


@runtime_checkable
class FormatWriter(Protocol):
    format_id: str
    extensions: tuple[str, ...]
    #: Validated-writes policy: False => CLI demands --experimental.
    validated: bool

    def write(self, doc: ir.Document, path: Path, fidelity: Fidelity,
              options: dict[str, Any] | None = None) -> None: ...
