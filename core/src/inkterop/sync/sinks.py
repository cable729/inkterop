"""Sync sinks: render one document into the output tree.

A sink invocation writes ALL files for one document (multi-page SVG/PNG
write one file per page) into a temp directory on the destination volume,
then moves them into place — iCloud and friends never see half-written
files, and the engine gets the exact output list for its state/cleanup.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ..formats.base import Fidelity

EXTENSIONS = {"pdf": ".pdf", "svg": ".svg", "png": ".png", "inkz": ".inkz"}


def write_doc(fmt: str, source, doc, out_dir: Path, out_name: str, *,
              render_config=None, pen_style: str = "faithful") -> list[Path]:
    """Render `doc` from `source` as `fmt` into out_dir/out_name.<ext>.

    Returns the absolute paths written (>=1; multi-page svg/png return one
    per page).
    """
    ext = EXTENSIONS.get(fmt)
    if ext is None:
        raise ValueError(f"unknown sink format {fmt!r}")
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
            prefix=".inkterop-tmp-", dir=out_dir) as td:
        tmp_target = Path(td) / f"{out_name}{ext}"

        if fmt == "pdf" and hasattr(source, "render_pdf_native"):
            # The pinned mirror render path (golden-test protected).
            source.render_pdf_native(doc, tmp_target, render_config)
        else:
            ir_doc = source.to_ir(doc, pen_style=pen_style)
            from .. import formats
            writer = formats.writer_for(tmp_target)
            options = None
            if fmt == "pdf" and render_config is not None \
                    and doc.source_id == "remarkable":
                options = {"render_config": render_config}
            writer.write(ir_doc, tmp_target, Fidelity.EXACT, options)

        written = []
        for f in sorted(Path(td).iterdir()):
            dest = out_dir / f.name
            shutil.move(str(f), dest)
            written.append(dest)
    if not written:
        raise RuntimeError(f"{fmt} sink produced no output for {doc.key}")
    return written
