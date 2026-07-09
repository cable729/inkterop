"""IR-JSON: inkterop's own lossless interchange format (.json).

This is the `--fidelity raw` flagship target for local tooling: the full
IR — raw per-point channels, appearance, and semantic tools together —
serialized as documented JSON. Reading it back is lossless.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import ir
from ..ir import serialize
from .base import Fidelity

FORMAT_ID = "irjson"


class IrJsonReader:
    format_id = FORMAT_ID
    extensions = (".json",)

    def detect(self, path: Path) -> bool:
        try:
            with open(path, "rb") as f:
                head = f.read(4096)
            return b'"inkterop_ir"' in head
        except OSError:
            return False

    def read(self, path: Path) -> ir.Document:
        return serialize.document_from_dict(json.loads(path.read_text()))


class IrJsonWriter:
    format_id = FORMAT_ID
    extensions = (".json",)
    validated = True  # our own format; round-trip covered by tests

    def write(self, doc: ir.Document, path: Path, fidelity: Fidelity,
              options: dict[str, Any] | None = None) -> None:
        # The IR dump always carries all three fidelity layers; the
        # `fidelity` knob has nothing to strip here.
        path.write_text(serialize.dumps(doc, indent=2, embed_attachments=True))
