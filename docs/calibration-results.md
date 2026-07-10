# Calibration pages — results log

Companion to `docs/calibration-pages.md` (the drawing script). The raw
pages live in the local, gitignored `corpus/calibration/` (see its
MANIFEST.md); this file carries the distilled findings so they survive
in-repo. Rerun the numbers with `core/scripts/calib_summary.py`
(channel statistics) and `core/scripts/fit_render_laws.py` (rendered-
width measurement + law fits), both run from `core/` with
`uv run python scripts/<name>.py`.

## Round 1 — 2026-07-10, all five apps

Sources: reMarkable Paper Pro (device), GoodNotes / Notability / Saber /
Nebo on iPad (iPadOS 26.5, Apple Pencil), extracted via the Mac apps.
Per app: native file + the app's own PDF export; SVG too for reMarkable
and Nebo. App versions in corpus MANIFEST.

### Per-tool channel statistics

`w` = stored per-point width channel (source units), `p` = pressure,
`corr` = median per-stroke Pearson correlation width~pressure.

**reMarkable** (widths are device-computed; true width = w/4 units):

| tool | n | width | pressure→width |
|---|---|---|---|
| ballpoint | 8 | 2.0–3.0 | corr +0.90 |
| fineliner | 25 | const 4.0 | none |
| marker | 39 | 5.0–9.0 | corr +0.93 |
| pencil | 8 | 4.0–6.0 | corr +0.97 |
| mechanical_pencil | 8 | const 6.0 | none (alpha varies instead?) |
| brush/paintbrush | 20 | 4.0–9.25 | corr +0.78 |
| calligraphy | 16 | 2.0–10.5 | corr +0.51 → tilt suspected as the real driver |
| shader | 13 | 7.25–22.0 | corr +0.74 |
| highlighter | 8 | const 30.0 | none |

Confirms the repo doctrine: trust the stored width channel; the device
already folds pressure/speed/tilt into it. Only calligraphy needs a
second input — regress its width against `tilt_azimuth` using the
tilt-pair probes (next).

**Saber** (iPad/Mac 1.35):
- fountain pen: pressure-enabled, per-point width; size-extreme rows
  give base sizes 1.0 / 5.0 / 25.0.
- ballpointPen (new tool name, mapped 2026-07-10): constant 5.0,
  pressure disabled. pencil: constant width + pressure channel.
  highlighter: constant 50.0.

**Notability** (.ntb v16): no pressure channel — pressure is baked into
per-point widths (pen 0.42–10.59; pencil/highlighter constant). The red
baseline stroke **verified R-before-G color byte order** (doc updated;
.ntb writer validation ungated).

**GoodNotes** (journal-25 export): per-point widths only (0.09–24.0).
Two reader gaps found — see "In-flight work" below.

**Nebo** (iPad 7.4.3, Apple Pencil): first sample with real force data
(all capacitive corpus was f=255). Constant appearance width from brush
name (pen-025 = 0.25 mm, brush-0500 = 5 mm); `.STYLE` carries
`-myscript-pen-pressure-sensitivity: 0.8` (0 for highlighter).
Decoder fixes landed (commit a684a07): stroke tombstones, tag-table
span-group counts, tag indices counting tombstones.

### Fitted rendering laws (round-1 analysis, 2026-07-10)

Method: match each app's own vector export to the native strokes by
centroid, then measure the rendered ribbon width by ray-casting
perpendicular to the local stroke direction through the outline
polygons (even-odd parity). Fitted constants live in
`core/src/inkterop/ir/renderrule.py`; laws recorded in the format docs.

**reMarkable** (oracle: desktop 3.27.2 SVG export; viewBox = canvas
units, so widths compare directly):

| tool | export geometry | rendered / stored WIDTH |
|---|---|---|
| fineliner | stroked, `stroke-width` attr | **1.000 exactly** |
| highlighter | stroked | **1.000 exactly** |
| ballpoint | filled outline | 1.016 |
| calligraphy | filled outline | 1.024 |
| shader | filled outline | 0.996–1.009 |
| marker | filled outline | 0.82 (soft edge) |
| brush | filled outline | 0.78 (soft edge) |
| mechanical pencil | filled outline | 0.69 (texture) |
| pencil | filled outline | 0.61 (texture) |

Confirms the WIDTH channel **is** the rendered width `[verified]`; the
soft-edge/texture tools' export outlines trace an opacity threshold
inside the nominal width (their high ratios on self-overlap probes and
weak per-point tracking say smoothing/edge softness, not a different
width law) `[inferred]`.

**reMarkable calligraphy driver**: width is computed from stroke travel
direction against a fixed nib axis + pressure — **tilt refuted** (the
suspected `tilt_azimuth` driver is near-zero throughout and does not
correlate). Fit (R²=0.54, 313 pts):
`w/ts = 2.855 − 2.176·|sin(θ − 92°)| + 1.004·p`. Single
thickness_scale (2.0) in sample, so ts-proportionality `[inferred]`.

**Nebo/MyScript** (oracle: the app's SVG export, mm units): pens render
`width = base × (1 + sensitivity × 2.43 × (force − 0.29))`. Bin
medians (rendered/base at sensitivity 0.8): f0.0→0.34, f0.2→0.77,
f0.4→0.96, f0.6→1.75, f0.8→2.27, f1.0→2.27. Highlighter
(sensitivity 0) renders constant 1.06×base — the same law at s=0, so
the sensitivity parametrization holds at both sampled values
`[verified at s∈{0, 0.8}]`. The Nebo reader now bakes this into
per-point WIDTH when force varies. Side discoveries: the thick-brush
row (5 mm) renders ~5.3 mm but our reader mis-attributes it 0.25 mm —
the tag-run gap (open Q1) in action; and our Nebo→PDF render uses the
wrong page size (Letter vs A4), which currently sinks the registered
visualdiff vs the app's PDF export (~17% ink-match, pure
misregistration — spun off as a background task).

### Open questions raised

1. Nebo: does a HIGHLIGHT_STROKES / brush tag legally cover a RUN of
   strokes? The drawn highlighter row has 8 strokes but only the
   anchor stroke (record 35) is tagged; the reader styles only that
   one today. Needs a controlled sample (draw N highlighter strokes,
   observe tag records). Round-1 analysis confirms the cost: the
   brush row renders 5 mm in-app but reads as 0.25 mm pen strokes.
2. ~~reMarkable calligraphy width law: fit against tilt.~~ Answered —
   the driver is stroke direction + pressure, not tilt (see above).
3. ~~MyScript rendered-width law.~~ Answered — fitted (see above).

### Next analysis steps (rendering-law fitting)

1. ~~Parse each app's own vector export and fit width(channel) per
   tool.~~ Done for reMarkable + Nebo (above). Notability/Saber/
   GoodNotes have only PDF exports — measure via the content-stream
   dump used by the golden tests; Saber can also be read from its
   open-source renderer.
2. GoodNotes per-style analysis is blocked on the two reader gaps
   (below), then redo the per-tool table per pen style.

## In-flight work (started 2026-07-10, separate sessions/worktrees)

Two background sessions were spawned off this analysis — check their
branches/results before re-attempting:

1. **Decode GoodNotes events-only pages in reader** — exports can
   contain a 0-byte `notes/<uuid>` blob with the page's strokes living
   only in `index.events.pb`; the reader needs event replay (page 1 of
   `corpus/calibration/goodnotes-calibration.goodnotes` reproduces).
2. **Decode GoodNotes pen-style records** — all strokes currently map
   to generic PEN with empty params; style lives in the undecoded
   stroke/tpl sections (page 2 of the calibration notebook has
   fountain/ball/brush/highlighter/size-extreme/red rows to RE
   against).
