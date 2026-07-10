"""Pixel-level comparison of rendered ink ("golden screenshot" harness).

`raster` turns PDFs into images, `diff` compares images with a
noise-tolerant pixel metric, `png` exposes rasterization as a registered
format writer so `inkterop convert doc.rm out.png` works.

Thresholds live in `diff.py` and are set by measurement, not convention —
see README.md in this directory for the calibration data behind them.
"""
from .diff import DiffResult, compare  # noqa: F401
from .raster import pdf_pages_to_images  # noqa: F401
