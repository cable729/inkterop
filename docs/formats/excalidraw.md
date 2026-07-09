# Excalidraw (.excalidraw)

Open JSON scene format of the MIT-licensed Excalidraw whiteboard
(excalidraw.com / VS Code extension). Read **and** write:
`core/src/inkterop/formats/excalidraw.py`. Schema facts from
docs.excalidraw.com and the MIT source's field names; load-checked
2026-07-09 against the official `@excalidraw/excalidraw` 0.18.0 package
(`loadFromBlob` — the app's file-open path — plus `exportToSvg` visual
comparison), which upgraded the envelope/element facts to `[verified]`
and exposed the freedraw rendering law below.

## Container

Plain JSON: `{"type": "excalidraw", "version": 2, "elements": [...],
"appState": {...}, "files": {...}}`. `detect()` = `"excalidraw"` marker in
the first 4 KB of a JSON object.

## Ink model

- `freedraw` elements: `x`/`y` origin + relative `points` `[[dx,dy],…]`;
  `pressures` (0–1, one per point) unless `simulatePressure` is true (then
  empty and the app synthesizes pressure from speed); `strokeColor` hex,
  `strokeWidth`, `opacity` 0–100, `angle` radians about the element
  center.
- **Freedraw rendering law** `[verified]`: the app draws freedraw ink via
  perfect-freehand, and `strokeWidth` is NOT the on-canvas thickness.
  Measured against `@excalidraw/excalidraw` 0.18.0 `exportToSvg` with
  constant-pressure probe strokes (fits to 3 significant digits):

  ```
  thickness(p) = strokeWidth × 8.5 × sin(π/2 × (0.5 + 0.6·(p − 0.5)))
  ```

  i.e. 8.08× strokeWidth at p=1.0, 6.01× at p=0.5 (probes: sw 10 → 80.8
  / 60.1 px). `simulatePressure` strokes measured ~6.9× on a
  uniform-speed probe (speed-dependent — approximate). The in-app pen
  sizes S/M/L are strokeWidth 1/2/4. Constants live in
  `formats/excalidraw.py:_thickness_factor`.
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

**Widths** go through the rendering law in both directions. Reader:
`Channel.WIDTH[i] = strokeWidth × thickness_factor(pressures[i])` (or
×6.9 constant for `simulatePressure`); shape/line elements stroke 1:1.
Writer (EXACT/NATIVE): re-encodes the IR `WIDTH` channel as synthetic
`pressures` through the inverse law — the widest point maps to p=1.0 —
so the app reproduces per-point widths; a constant-width pen with
varying pressure stays constant in-app. Points narrower than ~0.31× the
stroke's max width clamp at the law's floor.
excalidraw→excalidraw round-trips still preserve original pressures
exactly (the reader derived WIDTH from them, so the inversion returns
the same values); RAW fidelity writes source pressures as-is.

**Opacity**: element `opacity` ← median of the IR `ALPHA` channel when
present (excalidraw has no per-point alpha — reMarkable pencil texture
flattens to its median), else `appearance.opacity`.

Writer emits every IR stroke as `freedraw` (foreign shapes arrive as
polylines anyway), texts as `text` elements; multi-page IR docs flatten to
page 1 (documented limitation — Excalidraw has no pages). Deterministic
ids/seeds; `validated=True` (see checklist row in
`docs/validated-writes.md`).

## Open questions

1. `image` elements + `files` data-URLs → `RasterImage` (not yet read).
2. Bound-text containers (labels inside shapes) are read as free text.
3. The `simulatePressure` 6.9× factor is speed-dependent; our constant is
   a uniform-speed measurement `[inferred]` for real hand-drawn strokes.

## Changelog

- 2026-07-09: initial reader+writer from documented schema; hand-authored
  CC0 fixture; round-trip + foreign-conversion tests.
- 2026-07-09 (later): freedraw rendering law measured against the
  official 0.18.0 package; width mapping fixed (was 1:1 → rendered ~8×
  fat); ALPHA-channel opacity; per-point width → synthetic-pressure
  encoding; writer validated (loadFromBlob + visual match vs the
  golden-validated renderer on `fineliner-pencil-colors.rm`).
