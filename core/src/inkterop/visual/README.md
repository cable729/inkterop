# visual/ — pixel-diff harness calibration

The thresholds in `diff.py` and the visual tests are **measured, not
assumed**. This file records the calibration method and the data behind
the current values. Re-run with:

```sh
uv run python scripts/calibrate_visualdiff.py
```

## Metric

`compare()` reports two ratios per page:

- `match_ratio` — fraction of all pixels whose per-channel delta is within
  `pixel_tolerance` (default 24/255);
- `ink_match_ratio` — the same fraction over only the pixels that carry ink
  in either image (luminance < 235 on the *unblurred* images). This is the
  ratio that matters: a dropped stroke barely moves `match_ratio` on a
  mostly-white page but craters `ink_match_ratio`.

Modes:

- **strict** — equal dimensions, no preprocessing. For goldens: the
  renderer is deterministic, so the noise floor is exactly 100%.
- **registered** — for cross-rasterizer comparison: crop both images to
  their ink bounding box, LANCZOS-rescale the candidate onto the
  reference, then search integer shifts (±3 px) for the one that
  minimizes differing-ink count (bbox rounding differs per rasterizer;
  a 1 px offset otherwise reads as a huge mismatch on thin strokes),
  and Gaussian-blur both by radius 2 before diffing (turns residual
  subpixel misalignment into deltas the tolerance absorbs). Blur radius
  is calibrated at 96 dpi — scale it if you change dpi.

## Calibration results (2026-07-09, darwin, dpi 96, tolerance 24, blur 2)

Group A = noise floor (must pass), B = injected bugs (must fail),
C = cross-app (informational until per-app rendering rules are measured).

| group | case | match | ink-match |
|---|---|---|---|
| A noise | fineliner: rerender (strict) | 100.0000% | 100.0000% |
| A noise | fineliner: self registered | 100.0000% | 100.0000% |
| A noise | fineliner: 96 vs 192 dpi, registered | 99.9812% | 99.9092% |
| A noise | gn-mixed-pens: rerender (strict) | 100.0000% | 100.0000% |
| A noise | gn-mixed-pens: self registered | 100.0000% | 100.0000% |
| A noise | gn-mixed-pens: 96 vs 192 dpi, registered | 99.6178% | 96.5950% |
| B bug | fineliner: width x2 | 84.0320% | 37.5602% |
| B bug | fineliner: dropped stroke | 99.9981% | **99.9862%** |
| B bug | fineliner: color swap R<->G | 97.4537% | 81.0432% |
| B bug | fineliner: opacity halved | 99.9547% | 99.6629% |
| B bug | fineliner: translate 1% | 94.7788% | 66.1882% |
| B bug | gn-mixed-pens: width x2 | 94.9409% | 50.2692% |
| B bug | gn-mixed-pens: dropped stroke | 99.7742% | 94.0360% |
| B bug | gn-mixed-pens: color swap R<->G | 97.1402% | 24.6468% |
| B bug | gn-mixed-pens: opacity halved | 93.0604% | 3.2977% |
| B bug | gn-mixed-pens: translate 1% | 95.4305% | 37.5979% |
| C cross-app | gn-mixed-pens vs GoodNotes Mac export | 47.3017% | 5.2305% |
| C cross-app | saber-pens-text vs Saber Mac export | 77.9663% | 3.7890% |

## Decisions

- **Strict golden threshold: ink-match >= 99.99%** (`test_visual_golden.py`).
  The floor is exactly 100% (deterministic renderer + timestamp-free PNGs);
  the hardest bug to catch — one dropped stroke on a dense page — scores
  99.986%, safely below the bar. Caveat: goldens are generated on darwin;
  if ubuntu CI's pdfium wheel antialiases differently the bar may need a
  platform re-check — investigate before loosening, never just lower it.
- **Registered-mode noise floor: ~96.6% ink-match** (worst same-content
  probe). Any cross-app pass bar must sit below this ceiling. Per-app pass
  bars are set only after that app's rendering rule is measured; until
  then group C is a scorecard, not a gate.
- **Group C scores are dominated by known, itemized gaps**, not metric
  noise: the app exports draw paper templates our readers don't yet emit
  (both apps), and stroke widths go through per-app rendering rules that
  are still unmeasured for GoodNotes/Saber. These numbers are the baseline
  the rendering-rule workstream exists to raise.
