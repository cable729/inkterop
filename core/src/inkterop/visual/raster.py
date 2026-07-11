"""PDF -> PIL images via pypdfium2.

Pages are rendered at `supersample` x the requested dpi and box-downsampled,
which damps antialiasing jitter so the diff metric measures geometry and
color, not rasterizer edge noise.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

DEFAULT_DPI = 150
DEFAULT_SUPERSAMPLE = 2


def page_count(path: Path | str) -> int:
    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path))
        try:
            return len(doc)
        finally:
            doc.close()


#: PDFium itself is not thread-safe; the sync daemon rasterizes from
#: several threads (thumbnails + png sink), so all pdfium use serializes.
_PDFIUM_LOCK = threading.Lock()


def pdf_pages_to_images(path: Path | str, dpi: int = DEFAULT_DPI,
                        supersample: int = DEFAULT_SUPERSAMPLE,
                        pages: list[int] | None = None) -> list[Image.Image]:
    """Rasterize PDF pages to RGB images at `dpi`.

    `pages` selects 0-based page indices (default: all).
    """
    with _PDFIUM_LOCK:
        return _pdf_pages_to_images(path, dpi, supersample, pages)


def _pdf_pages_to_images(path, dpi, supersample, pages):
    doc = pdfium.PdfDocument(str(path))
    try:
        indices = pages if pages is not None else range(len(doc))
        out: list[Image.Image] = []
        for i in indices:
            page = doc[i]
            scale = dpi * supersample / 72.0
            bitmap = page.render(scale=scale)
            img = bitmap.to_pil().convert("RGB")
            if supersample > 1:
                img = img.resize((max(1, img.width // supersample),
                                  max(1, img.height // supersample)),
                                 Image.Resampling.BOX)
            out.append(img)
        return out
    finally:
        doc.close()
