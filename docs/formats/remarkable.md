# reMarkable v6 / Paper Pro format notes (reverse-engineered)

Everything here was verified empirically on 2026-07-08 against a real
Paper Pro notebook (landscape, firmware-era 3.2x) and its OFFICIAL desktop
export (pages 685pt wide, heights 514–925pt). Where this contradicts
rmc/community docs, trust this file — the community model is rM2-era.

## Library cache layout (desktop app)

`~/Library/Containers/com.remarkable.desktop/Data/Library/Application Support/remarkable/desktop/`

- `<uuid>.metadata` — JSON: `visibleName`, `parent` (uuid | "" | "trash"),
  `type` (DocumentType|CollectionType), `lastModified` (ms epoch string).
- `<uuid>.content` — JSON: `fileType` (notebook|pdf|epub), `orientation`,
  `cPages.pages[]` with `id` (page .rm filename), optional `deleted`,
  `template.value` (e.g. "Blank", "P Dots S"). NOTE:
  `customZoomPageWidth/Height` say 1404x1872 — legacy values, WRONG for
  actual stroke coordinates.
- `<uuid>/<page-uuid>.rm` — v6 scene files ("reMarkable .lines file,
  version=6" ASCII header).

## Coordinate system (Paper Pro)

- Strokes are in **display orientation**: x horizontal centered on 0,
  y down from 0. Landscape notebooks need NO rotation (unlike the rM2
  portrait-canvas model in rmc).
- Nominal canvas: portrait x ∈ [-810, 810], y ∈ [0, 2160]; landscape
  x ∈ [-1080, 1080], y ∈ [0, 1620].
- "Adjustable page height": content grows y past the nominal height.
  Official export page height = max(nominal, y_max + ~48 units) — verified
  within ~2% on all 15 pages.
- Export scale: **685pt / 2160units** (≈0.3171 pt/unit, ≈227 DPI).

## How the official export draws (from its PDF content streams)

Extract with `pikepdf.parse_content_stream(page)`; count `w` (linewidth),
`S` (stroke), `f` (fill), `rg` (color), `gs` (ExtGState) ops.

| Pen | Official rendering |
|---|---|
| Fineliner | stroked polylines, constant width = `point.width/4` units (0.634pt for size-2), solid color |
| Ballpoint, Calligraphy (variable-width pens) | **filled outline polygons** (no `w` ops at all), solid color |
| Highlighter | stroked, width `point.width/4` (30 units = 9.5pt), **opacity 1.0 + `/BM /Darken` blend**, color = stroke's `color_rgba` exactly |

## The one rule for stroke width

**Each point stores the device-computed rendered width. True width =
`point.width / 4` canvas units.** (rmscene decodes width as
`int(round(f32*4))`.) Observed: fineliner constant 8 (→2u); ballpoint
8–12; calligraphy 12–64; highlighter constant 120 (→30u).

Do NOT apply rmc's pressure/speed/tilt width formulas to v6 files — they
were reverse-engineered for older formats where width wasn't stored, and
applying them here double-counts pressure (calligraphy → blobs) and
subtracts speed (ballpoint → hairlines that antialias gray).

Our renderer (`core/src/inkterop/render.py`) approximates the official
filled-outline approach by splitting variable-width strokes into
constant-width runs (tolerance 0.35u) drawn with round caps; highlighters
draw beneath ink at alpha 0.85 to approximate /Darken without ExtGState
surgery. If pixel-perfect blending is ever needed: post-process with
pikepdf and set `/BM /Darken` on the highlighter ExtGState.

## Colors

- `line.color` is a PenColor enum (14-color palette in
  `core/src/inkterop/pens.py`); `line.color_rgba` (when present) is exact
  RGBA and wins. Highlighters always carry `color_rgba` on Paper Pro
  (observed: yellow 255,237,117; green 172,255,133; orange 255,195,140;
  gray 199,199,198).

## Templates

- Per page: `.content` `cPages.pages[].template.value`. Observed "Blank",
  "P Dots S". The official desktop export DROPS templates entirely (we
  want them, so we draw them).
- Real template art lives on-device at `/usr/share/remarkable/templates/`
  (SVGs) — grab during a rooted session. Until then our dot pitch
  (39 units) and line spacing are visual approximations.
- Templates tile across the FULL grown page, anchored at the canvas origin.

## Gotchas

- rmscene 0.8.0 logs "Some data has not been read" on newer firmware blocks
  — harmless (unknown blocks preserved); don't treat as failure.
- Pages with no drawn content have no `.rm` file → emit a blank page.
- `cPages.pages[]` entries with a `deleted` key are deleted pages — skip.
- Text blocks (typed text) exist in v6 (rmscene `root_text`); we use them
  only for stroke anchor positions so far, not rendered.

## IR mapping (what the reader emits)

Reader: `core/src/inkterop/formats/remarkable/reader.py`. One `ir.Stroke`
per `si.Line`, coordinates with text-anchor offsets applied.

Channels: `WIDTH` = `PenModel.width(p)` (i.e. `point.width/4`, floor 0.5u,
faithful style), raw `PRESSURE` (`p.pressure/255`), `SPEED` (`p.speed`,
device units), `TILT_AZIMUTH` (`p.direction * 2pi/255` rad); `ALPHA` only
for pencils (pressure-derived opacity varies per point). Constant opacity
lives in `appearance.opacity` instead.

Tool families (`_FAMILY`): BALLPOINT_1/2->ballpoint, CALIGRAPHY->calligraphy,
ERASER/ERASER_AREA->eraser, FINELINER_1/2->fineliner,
HIGHLIGHTER_1/2->highlighter, MARKER_1/2->marker,
MECHANICAL_PENCIL_1/2->mechanical_pencil, PAINTBRUSH_1/2->brush,
PENCIL_1/2->pencil, SHADER->shader. `NativeTool` preserves the raw enum +
color enum + `color_rgba` + `thickness_scale` for lossless round-trip.

Appearance: fineliner/highlighter -> `STROKED_CONSTANT` (width = first
point's); others `STROKED_VARIABLE`; highlighter/shader get
`blend=DARKEN, cap=SQUARE, underlay=True` (opacity 0.85 / 0.45);
`ERASER_AREA` opacity 0. `pen_style="rmc"` is a reader option: rmc width
formulas fill WIDTH, and per-point ballpoint colors go to
`extra["inkterop"]["point_rgb"]`.

## Renderer quirks that goldens pin (do not "fix" casually)

Port of the validated renderer lives in `core/src/inkterop/render/`
(`primitives.py` + `pdf.py`); output verified op-identical on the whole
library (110/110 docs, scripts/ab_check.py).

- Variable-width strokes are split into constant-width polyline runs when
  the per-point width drifts more than **0.35u** from the run's width;
  adjacent runs share the split point; a run only closes once it has >=2
  points.
- **Color/alpha are sampled at run starts only**, not per point (a pencil
  stroke's opacity steps at width splits — matches the validated output).
- Single-point runs render as filled circles, r = width/2 (zero-length
  round-cap segments draw nothing in PDF).
- Highlighter/shader (underlay) strokes draw BENEATH ink at partial
  opacity — an approximation of the official export's `/BM /Darken`
  ExtGState (exact-blend pikepdf pass is an M2 item).
- Strokes whose first-point alpha <= 0 (ERASER_AREA) are skipped entirely
  and excluded from page-bounds computation.
- Page CTM folds `point_scale` in (`transform(s*scale, 0, 0, -s*scale, ...)`)
  so widths are set in canvas units; blank pages are emitted at target
  size even when `normalize="native"`.
