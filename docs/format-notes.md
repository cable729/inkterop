# reMarkable v6 / Paper Pro format notes (reverse-engineered)

Everything here was verified empirically on 2026-07-08 against Caleb's
validation notebook (landscape, Paper Pro, firmware-era 3.2x) and
its OFFICIAL export (`the official export PDF` at the time; pages
685pt wide, heights 514–925pt). Where this contradicts rmc/community docs,
trust this file — the community model is rM2-era.

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

Our renderer (`core/src/rminterop/render.py`) approximates the official
filled-outline approach by splitting variable-width strokes into
constant-width runs (tolerance 0.35u) drawn with round caps; highlighters
draw beneath ink at alpha 0.85 to approximate /Darken without ExtGState
surgery. If pixel-perfect blending is ever needed: post-process with
pikepdf and set `/BM /Darken` on the highlighter ExtGState.

## Colors

- `line.color` is a PenColor enum (14-color palette in
  `core/src/rminterop/pens.py`); `line.color_rgba` (when present) is exact
  RGBA and wins. Highlighters always carry `color_rgba` on Paper Pro
  (observed: yellow 255,237,117; green 172,255,133; orange 255,195,140;
  gray 199,199,198).

## Templates

- Per page: `.content` `cPages.pages[].template.value`. Observed "Blank",
  "P Dots S". The official desktop export DROPS templates entirely (Caleb
  wants them, so we draw them).
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
