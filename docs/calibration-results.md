# Calibration pages — results log

Companion to `docs/calibration-pages.md` (the drawing script). The raw
pages live in the local, gitignored `corpus/calibration/` (see its
MANIFEST.md); this file carries the distilled findings so they survive
in-repo. Rerun the numbers with `core/scripts/calib_summary.py` (run
from `core/`: `uv run python scripts/calib_summary.py`).

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

### Open questions raised

1. Nebo: does a HIGHLIGHT_STROKES / brush tag legally cover a RUN of
   strokes? The drawn highlighter row has 8 strokes but only the
   anchor stroke (record 35) is tagged; the reader styles only that
   one today. Needs a controlled sample (draw N highlighter strokes,
   observe tag records).
2. reMarkable calligraphy width law: fit against tilt using the
   tilt-pair probes (per-point `tilt_azimuth` channel is present).
3. MyScript rendered-width law: now fittable — F channel +
   pressure-sensitivity + the app's own SVG export as oracle
   (`corpus/calibration/nebo-calibration.app-export.svg`).

### Next analysis steps (rendering-law fitting)

1. Parse each app's own vector export (rM SVG, Nebo SVG; PDFs via the
   content-stream dump used by the golden tests) and measure RENDERED
   stroke widths along the baseline / pressure-ramp / speed-sweep
   probes; fit width(channel) per tool. This is the measurement the
   calibration pages exist for — record laws per
   `docs/formats/STYLE.md` (protocol.md/rendering.md split) with
   [verified] markers.
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
