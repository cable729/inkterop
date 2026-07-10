"""PNG writer: rasterized render of the IR (via the pinned PDF renderer).

Single-page documents write `out.png`; multi-page write `out.png`,
`out-2.png`, `out-3.png`, ... PNGs carry no timestamps, so output is
byte-deterministic — safe to commit as goldens.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .. import ir


class PngWriter:
    format_id = "png"
    extensions = (".png",)
    validated = True  # display format; look is pinned by the golden tests

    def write(self, doc: ir.Document, path: Path, fidelity,
              options: dict[str, Any] | None = None) -> None:
        from ..render.pdf import PdfWriter
        from .raster import DEFAULT_DPI, pdf_pages_to_images

        options = options or {}
        dpi = options.get("dpi", DEFAULT_DPI)
        with tempfile.TemporaryDirectory() as td:
            tmp_pdf = Path(td) / "render.pdf"
            PdfWriter().write(doc, tmp_pdf, fidelity, options)
            images = pdf_pages_to_images(tmp_pdf, dpi=dpi)
        path.parent.mkdir(parents=True, exist_ok=True)
        for i, img in enumerate(images):
            out = path if i == 0 else path.with_stem(f"{path.stem}-{i + 1}")
            img.save(out, format="PNG")
