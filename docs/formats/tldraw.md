# tldraw (.tldr)

JSON save file of the tldraw whiteboard (tldraw.com / the tldraw SDK).
**Read only**: `core/src/inkterop/formats/tldraw.py`.

License caution: tldraw's source is under a custom source-visible
license, so — like the GoodNotes/goodparse boundary — this reader was
built **only** from the public tldraw.dev docs pages plus hand-authored
sample files. No tldraw source code was read. Palette / size / rendered
ink-thickness facts were upgraded to `[verified]` on 2026-07-09 by
**runtime observation of the released npm packages** (reading exported
constants and measuring a mounted editor's `getSvgString` output in a
browser — the devtools-on-tldraw.com method, applied to the SDK), which
stays on the safe side of the source-visible boundary.

## Container

Plain JSON `[verified against the documented file shape]`:

```json
{"tldrawFileFormatVersion": 1, "schema": {...}, "records": [...]}
```

`detect()` = `"tldrawFileFormatVersion"` key in the first 4 KB of a
JSON object (discriminates from `.excalidraw`, the other JSON canvas).

`records` is a flat list; each record has `typeName` ("document",
"page", "shape", "camera", "instance", "asset", ...). Shapes reference
their page via `parentId` (`"page:<id>"`, or `"shape:<id>"` when
nested inside a frame/group — the reader walks the chain to find the
owning page). Page records carry `name` and a fractional-indexing
`index` key; lexicographic order of those keys is the page order
`[inferred]`. Shapes are z-ordered by the same kind of `index` key.

## Ink model

`shape` records of `type` `"draw"` (pen tool) and `"highlight"`
(highlighter) `[inferred from tldraw.dev docs]`:

- `x`/`y` shape origin, `rotation` radians about the origin.
- `props.segments[]`, each `{type: "free"|"straight", points: [...]}`;
  points are `{x, y, z}` relative to the origin. Consecutive segments
  share their junction point (deduped on read).
- `z` = pressure 0–1; a constant 0.5 when no pressure device.
  `props.isPen` marks real stylus input.
- `props.color`: palette token (black/grey/light-violet/violet/blue/
  light-blue/yellow/orange/green/light-green/light-red/red/white).
- `props.size`: width token — `STROKE_SIZES` s=2, m=3.5, l=5, xl=10
  `[verified: tldraw 3.13.1 runtime export]`, multiplied by
  `props.scale`. **This is not the rendered thickness** — see below.
- `props.isClosed` closes the polyline; `props.isComplete` marks the
  stroke finished (ignored — incomplete strokes still render).

`"text"` shapes carry `props.text` (older) or ProseMirror-style
`props.richText` (`doc` → `paragraph` → `text` nodes); both are read,
rich text flattened to plain text with paragraph newlines. Font size
tokens: s=18, m=24, l=36, xl=44 px `[verified: FONT_SIZES export]`.

Palette hex values (default light theme) `[verified 2026-07-09 against
@tldraw/tlschema 3.13.1's runtime DefaultColorThemePalette export]`:
black `#1d1d1d`, grey `#9fa8b2`, light-violet `#e085f4`, violet
`#ae3ec9`, blue `#4465e9`, light-blue `#4ba1f1`, yellow `#f1ac4b`,
orange `#e16919`, green `#099268`, light-green `#4cb05e`, light-red
`#f87777`, red `#e03131`, white `#ffffff`.

Highlight shapes use the theme's **highlight swatches**, not the solid
colors `[verified, same export]` (srgb): black/yellow `#fddd00`, grey
`#cbe7f1`, light-violet `#ff88ff`, violet `#c77cff`, blue `#10acff`,
light-blue `#00f4ff`, orange `#ffa500`, green `#00ffc8`, light-green
`#65f641`, light-red `#ff7fa3`, red `#ff636e`, white `#ffffff`.

## Rendered ink thickness `[verified]`

Measured 2026-07-09 on a mounted tldraw 3.13.1 editor via
`getSvgString` probes (straight horizontal strokes, constant z):

- **draw**, z=0.5 (neutral): `thickness = 1.374 × STROKE_SIZES + 2.52`
  — 5.27 / 7.33 / 9.39 / 16.26 px for s/m/l/xl (exact affine fit).
  At z=1.0 the thickness is 1.503× neutral; the reader interpolates
  linearly per point (`1 + 1.006·(z − 0.5)`; below z=0.5 this is an
  extrapolation `[inferred]`). Constant-z strokes export as a stroked
  centerline path; varying-z as a filled outline.
  `isPen: false` (simulated pressure) measured 6.01 px for size m on a
  uniform-speed probe — speed-dependent; the reader uses the neutral
  law `[inferred]`.
- **highlight**: `thickness = 1.12 × FONT_SIZES` — 20.16 / 26.88 /
  40.32 / 49.28 px for s/m/l/xl (exact fit; answers old open question
  "highlighter width multiplier"). Drawn as two stacked passes of the
  highlight swatch at opacity 0.35 and 0.82 ⇒ combined coverage
  ≈ 0.883, which the reader uses as the stroke opacity.

## IR mapping

Infinite y-down CSS-px canvas → one `ir.Page` per tldraw page record,
content-bbox bounds + 20 px pad, `point_scale = 0.75` (same as
excalidraw). Page name in `Page.extra["name"]`.

- draw → `PEN`, highlight → `HIGHLIGHTER` (underlay + DARKEN blend),
  both with `NativeTool("tldraw", <type>)` carrying the raw tokens.
- `z` → `Channel.PRESSURE`, but only when `isPen` is true or the z
  values vary — a constant 0.5 is a placeholder, not signal.
- Shape-level `opacity` → appearance opacity.
- **Skipped shape types** (deliberate scope decision): `geo`, `line`,
  `arrow`, `frame`, `note`, `image`, `embed`, etc. tldraw's geometry
  shapes have rich props (arrowheads, cloud/star geo kinds, elbow
  arrows, bindings) whose faithful flattening is real work for little
  ink value; counts are recorded in
  `Document.metadata["skipped_shapes"]` and logged.

## No writer — rationale

tldraw's store schema migrates frequently: every record type carries
its own version in `schema.sequences` and the app runs explicit
migrations on load. A writer would have to pin one snapshot of that
churn and emit version numbers we cannot verify without reading the
(license-restricted) source; a stale or wrong sequence map is exactly
how third-party files break on open. Read-side only, by design. Export
to tldraw is better served by SVG (tldraw imports SVG/images).

## Open questions

1. An app-made `.tldr` (drawn at tldraw.com, saved) would confirm the
   record shapes our hand-authored fixture assumes — the remaining
   `[inferred]` container facts.
2. Frame/group children: page resolution walks the parent chain, but
   child coordinates are treated as page-absolute — nested-transform
   accumulation `[unknown]`.
3. Pressure→thickness below z=0.5 is a linear extrapolation from the
   z=0.5/z=1.0 measurements; a z<0.5 probe would pin it.
4. `isPen: false` simulated-pressure thickness is speed-dependent; we
   use the neutral-z law.

## Changelog

- 2026-07-09: initial reader from tldraw.dev docs; hand-authored CC0
  fixture (`core/tests/fixtures/tldraw/`); reader not yet registered in
  `formats/__init__.py` (workstream rule — registration lines reported
  in the PR).
- 2026-07-09 (later): palette (incl. highlight swatches), STROKE_SIZES,
  FONT_SIZES and the rendered-thickness laws verified against the
  released tldraw 3.13.1 packages at runtime; reader now emits measured
  rendered widths (was the raw size token — ~2.5× too thin), per-point
  pressure-scaled widths, highlight swatch colors and ~0.883 combined
  highlight opacity.
