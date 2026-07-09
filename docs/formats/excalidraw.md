# Excalidraw (.excalidraw)

Open JSON scene format of the MIT-licensed Excalidraw whiteboard
(excalidraw.com / VS Code extension). Read **and** write:
`core/src/inkterop/formats/excalidraw.py`. Schema facts from
docs.excalidraw.com and the MIT source's field names; marked `[inferred]`
until the fixture and a writer output are load-checked at excalidraw.com.

## Container

Plain JSON: `{"type": "excalidraw", "version": 2, "elements": [...],
"appState": {...}, "files": {...}}`. `detect()` = `"excalidraw"` marker in
the first 4 KB of a JSON object.

## Ink model

- `freedraw` elements: `x`/`y` origin + relative `points` `[[dx,dy],…]`;
  `pressures` (0–1, one per point) unless `simulatePressure` is true (then
  empty and the app synthesizes pressure from speed); `strokeColor` hex,
  `strokeWidth` px, `opacity` 0–100, `angle` radians about the element
  center. Rendering is perfect-freehand outline — our constant-width
  appearance is an approximation `[inferred]`.
- `line`/`arrow`: same `points` (arrowheads not modeled — dropped).
- `rectangle`/`ellipse`/`diamond`: implicit geometry, flattened to closed
  outline polylines on read (NativeTool keeps the element type).
- `text`: `text`/`fontSize` → `TextBlock`.
- `isDeleted: true` elements are skipped.

## IR mapping

Infinite y-down CSS-px canvas → single page, content-bbox bounds + 20 px
pad, `point_scale = 0.75`. `pressures` → `Channel.PRESSURE` (RAW fidelity
accepted both directions — pressure is the only raw channel the format
stores). freedraw → `PEN`; other element types → `UNKNOWN` +
`NativeTool("excalidraw", <type>)`.

Writer emits every IR stroke as `freedraw` (foreign shapes arrive as
polylines anyway), texts as `text` elements; multi-page IR docs flatten to
page 1 (documented limitation — Excalidraw has no pages). Deterministic
ids/seeds; `validated=False` pending the excalidraw.com open-check.

## Open questions

1. Load-check fixture + writer output at excalidraw.com (flips writer
   validated; upgrades field facts to `[verified]`).
2. `image` elements + `files` data-URLs → `RasterImage` (not yet read).
3. Bound-text containers (labels inside shapes) are read as free text.

## Changelog

- 2026-07-09: initial reader+writer from documented schema; hand-authored
  CC0 fixture; round-trip + foreign-conversion tests.
