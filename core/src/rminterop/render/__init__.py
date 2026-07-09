"""Output backends (IR -> PDF/SVG/...) + the legacy rendering entry point.

`render_notebook` keeps the pre-IR signature that mirror.py/cli.py call;
it now parses via formats.remarkable and draws via render.pdf.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .pdf import LETTER_LANDSCAPE, LETTER_PORTRAIT, RenderConfig, render_document

_logger = logging.getLogger(__name__)

__all__ = [
    "LETTER_LANDSCAPE",
    "LETTER_PORTRAIT",
    "RenderConfig",
    "render_document",
    "render_notebook",
]


def render_notebook(page_paths: list[Path], out_pdf: Path, landscape: bool,
                    config: RenderConfig | None = None,
                    templates: list[str] | None = None) -> None:
    """Render a notebook's .rm pages to a single PDF."""
    from .. import ir
    from ..formats.remarkable import read_page

    config = config or RenderConfig()
    templates = templates or [""] * len(page_paths)
    empty = ir.Page(bounds=ir.Rect(0, 0, 1, 1), point_scale=1.0)
    pages = []
    for rm_path, template in zip(page_paths, templates):
        page = empty
        if rm_path.exists():
            try:
                page = read_page(rm_path, landscape=landscape,
                                 template=template, pen_style=config.pen_style)
            except Exception:
                _logger.warning("failed to parse %s; blank page", rm_path,
                                exc_info=True)
        pages.append(page)
    doc = ir.Document(
        format_id="remarkable",
        orientation="landscape" if landscape else "portrait",
        pages=pages,
    )
    render_document(doc, out_pdf, config)
