# tldraw (.tldr)

JSON save file of the tldraw whiteboard (tldraw.com / the tldraw SDK).
**Read only**: `core/src/inkterop/formats/tldraw.py`.

License caution: tldraw's source is under a custom source-visible
license, so — like the GoodNotes/goodparse boundary — this reader was
built **only** from the public tldraw.dev docs pages plus hand-authored
sample files. No tldraw source code was read. Field facts are
`[inferred]` from docs until re-verified by loading fixtures at
tldraw.com.

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
- `props.size`: width token — s=2, m=3.5, l=5, xl=10 px `[inferred]`,
  multiplied by `props.scale`.
- `props.isClosed` closes the polyline; `props.isComplete` marks the
  stroke finished (ignored — incomplete strokes still render).

`"text"` shapes carry `props.text` (older) or ProseMirror-style
`props.richText` (`doc` → `paragraph` → `text` nodes); both are read,
rich text flattened to plain text with paragraph newlines. Font size
tokens: s=18, m=24, l=36, xl=44 px `[inferred]`.

Palette hex values used (default light theme, `[inferred]` from public
default-theme references — verifiable in-browser): black `#1d1d1d`,
grey `#9fa8b2`, light-violet `#e085f4`, violet `#ae3ec9`, blue
`#4465e9`, light-blue `#4ba1f1`, yellow `#f1ac4b`, orange `#e16919`,
green `#099268`, light-green `#4cb05e`, light-red `#f87777`, red
`#e03131`, white `#ffffff`.

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

1. Load the fixture at tldraw.com and draw an app-made sample back —
   upgrades palette/size/pressure facts to `[verified]` (palette values
   checkable in-browser via devtools on tldraw.com's own canvas).
2. Highlighter render width: the app draws highlight strokes much
   fatter than the size token's draw width `[unknown multiplier]`; we
   currently reuse the draw width table.
3. Dedicated highlight swatches: tldraw themes carry a separate
   `highlight` color per token; we use the solid swatch.
4. Frame/group children: page resolution walks the parent chain, but
   child coordinates are treated as page-absolute — nested-transform
   accumulation `[unknown]`.
5. Perfect-freehand outline rendering (tldraw, like Excalidraw, fills
   an outline polygon); our constant-width stroked appearance is an
   approximation.

## Changelog

- 2026-07-09: initial reader from tldraw.dev docs; hand-authored CC0
  fixture (`core/tests/fixtures/tldraw/`); reader not yet registered in
  `formats/__init__.py` (workstream rule — registration lines reported
  in the PR).
