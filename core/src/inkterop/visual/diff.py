"""Noise-tolerant image comparison for render-fidelity checks.

Two modes:

- ``strict`` — images must share dimensions (self-regression against
  committed goldens rendered at a fixed dpi).
- ``registered`` — each image is cropped to its ink bounding box and the
  second is rescaled onto the first before comparing (cross-app checks,
  where page sizes and margins legitimately differ).

Two ratios are reported, because a mostly-white page makes the whole-image
ratio insensitive:

- ``match_ratio`` — fraction of ALL pixels within tolerance;
- ``ink_match_ratio`` — the same fraction over only the pixels that carry
  ink in either image. A dropped stroke barely moves ``match_ratio`` but
  craters ``ink_match_ratio``.

Default thresholds are set by the calibration experiment recorded in
README.md next to this file, not assumed.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

#: Per-channel 0-255 delta below which two pixels count as "the same".
#: Calibrated: see README.md.
DEFAULT_PIXEL_TOLERANCE = 24
#: Luminance below which a pixel counts as ink (used for registration and
#: for the ink-weighted ratio). White paper is ~255; templates are light.
INK_LUMINANCE_MAX = 235


@dataclass
class DiffResult:
    match_ratio: float          # over all pixels
    ink_match_ratio: float      # over pixels that are ink in either image
    n_diff_pixels: int
    n_total_pixels: int
    n_ink_pixels: int
    size: tuple[int, int]
    aspect_warning: str | None = None
    diff_image: Image.Image | None = None

    def save_diff(self, path: Path | str) -> None:
        if self.diff_image is not None:
            self.diff_image.save(path)


def _to_array(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB"), dtype=np.int16)


def _ink_mask(arr: np.ndarray) -> np.ndarray:
    lum = arr @ np.array([0.299, 0.587, 0.114])
    return lum < INK_LUMINANCE_MAX


def content_bbox(img: Image.Image, margin: int = 2) -> tuple[int, int, int, int]:
    """Bounding box (left, top, right, bottom) of ink pixels, padded."""
    mask = _ink_mask(_to_array(img))
    if not mask.any():
        return (0, 0, img.width, img.height)
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    return (max(0, int(cols[0]) - margin), max(0, int(rows[0]) - margin),
            min(img.width, int(cols[-1]) + 1 + margin),
            min(img.height, int(rows[-1]) + 1 + margin))


#: Registered mode tries integer shifts up to this many px to line the
#: images up — content_bbox rounds differently per rasterizer, and a 1 px
#: global offset otherwise reads as a huge ink mismatch on thin strokes.
ALIGN_SEARCH_PX = 3


def _shift(arr: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """Shift with white fill (page background)."""
    out = np.full_like(arr, 255)
    h, w = arr.shape[:2]
    xs_src = slice(max(0, -dx), min(w, w - dx))
    ys_src = slice(max(0, -dy), min(h, h - dy))
    xs_dst = slice(max(0, dx), min(w, w + dx))
    ys_dst = slice(max(0, dy), min(h, h + dy))
    out[ys_dst, xs_dst] = arr[ys_src, xs_src]
    return out


def _best_alignment(img_a: Image.Image, img_b: Image.Image,
                    blur: float, tolerance: int) -> tuple[int, int]:
    """Integer shift of b that MINIMIZES the differing-ink count — i.e. it
    directly optimizes the criterion compare() scores with, so the search
    can only ever help the candidate, never hurt it."""
    from PIL import ImageFilter

    ink = _ink_mask(_to_array(img_a)) | _ink_mask(_to_array(img_b))
    f = ImageFilter.GaussianBlur(radius=blur) if blur else None
    a = _to_array(img_a.filter(f) if f else img_a)
    b = _to_array(img_b.filter(f) if f else img_b)
    best, best_bad = (0, 0), None
    r = ALIGN_SEARCH_PX
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            delta = np.abs(a - _shift(b, dx, dy)).max(axis=2)
            bad = int(((delta > tolerance) & ink).sum())
            if best_bad is None or bad < best_bad:
                best, best_bad = (dx, dy), bad
    return best


def _register(img_a: Image.Image, img_b: Image.Image, blur: float,
              tolerance: int) -> tuple[Image.Image, Image.Image, str | None]:
    a = img_a.crop(content_bbox(img_a))
    b = img_b.crop(content_bbox(img_b))
    warn = None
    ar_a = a.width / a.height
    ar_b = b.width / b.height
    if abs(ar_a - ar_b) / max(ar_a, ar_b) > 0.02:
        warn = f"aspect mismatch: {ar_a:.3f} vs {ar_b:.3f}"
    b = b.resize(a.size, Image.Resampling.LANCZOS)
    dx, dy = _best_alignment(a, b, blur, tolerance)
    if (dx, dy) != (0, 0):
        b = Image.fromarray(
            _shift(np.asarray(b.convert("RGB")), dx, dy), "RGB")
    return a, b, warn


#: Gaussian blur radius (px) applied to both images in registered mode.
#: Turns the subpixel misalignment inherent in cross-rasterizer comparison
#: into small value deltas the pixel tolerance absorbs. Calibrated at
#: 96 dpi: without it, the SAME document rendered at two dpis scores as
#: low as 68% ink-match; with it, >99%. Scale with dpi if you change dpi.
REGISTERED_BLUR_RADIUS = 2.0


def compare(img_a: Image.Image, img_b: Image.Image, *,
            mode: str = "strict",
            pixel_tolerance: int = DEFAULT_PIXEL_TOLERANCE,
            blur: float | None = None,
            make_diff_image: bool = True) -> DiffResult:
    """Compare two renders. `img_a` is the reference."""
    warn = None
    if mode == "registered":
        if blur is None:
            blur = REGISTERED_BLUR_RADIUS
        img_a, img_b, warn = _register(img_a, img_b, blur, pixel_tolerance)
    elif mode == "strict":
        if img_a.size != img_b.size:
            raise ValueError(
                f"strict compare needs equal sizes, got {img_a.size} vs "
                f"{img_b.size} (use mode='registered' for cross-app checks)")
    else:
        raise ValueError(f"unknown mode {mode!r}")

    # Ink masks come from the UNBLURRED images: blur exists to forgive
    # subpixel edge disagreement, not to shrink the ink denominator.
    ink = _ink_mask(_to_array(img_a)) | _ink_mask(_to_array(img_b))
    if blur:
        from PIL import ImageFilter
        f = ImageFilter.GaussianBlur(radius=blur)
        img_a, img_b = img_a.filter(f), img_b.filter(f)
    a, b = _to_array(img_a), _to_array(img_b)
    delta = np.abs(a - b).max(axis=2)
    differs = delta > pixel_tolerance

    n_total = differs.size
    n_diff = int(differs.sum())
    n_ink = int(ink.sum())
    ink_diff = int((differs & ink).sum())

    diff_img = None
    if make_diff_image:
        gray = (a @ np.array([0.299, 0.587, 0.114]))
        base = (191 + gray / 4).clip(0, 255).astype(np.uint8)  # washed-out A
        rgb = np.stack([base, base, base], axis=2)
        rgb[differs] = (220, 30, 30)
        diff_img = Image.fromarray(rgb, "RGB")

    return DiffResult(
        match_ratio=1.0 - n_diff / n_total if n_total else 1.0,
        ink_match_ratio=1.0 - ink_diff / n_ink if n_ink else 1.0,
        n_diff_pixels=n_diff,
        n_total_pixels=n_total,
        n_ink_pixels=n_ink,
        size=img_a.size,
        aspect_warning=warn,
        diff_image=diff_img,
    )


def compare_pdfs(path_a: Path | str, path_b: Path | str, *,
                 mode: str = "strict", dpi: int | None = None,
                 pixel_tolerance: int = DEFAULT_PIXEL_TOLERANCE,
                 report_dir: Path | None = None) -> list[DiffResult]:
    """Page-by-page comparison of two PDFs (pairs up to the shorter one;
    a page-count mismatch is reported by the caller comparing lengths)."""
    from .raster import DEFAULT_DPI, pdf_pages_to_images

    dpi = dpi or DEFAULT_DPI
    pages_a = pdf_pages_to_images(path_a, dpi=dpi)
    pages_b = pdf_pages_to_images(path_b, dpi=dpi)
    results = []
    for i, (pa, pb) in enumerate(zip(pages_a, pages_b)):
        r = compare(pa, pb, mode=mode, pixel_tolerance=pixel_tolerance)
        if report_dir is not None and r.diff_image is not None:
            report_dir.mkdir(parents=True, exist_ok=True)
            stem = Path(path_b).stem
            r.save_diff(report_dir / f"{stem}.p{i + 1}.diff.png")
        results.append(r)
    return results
