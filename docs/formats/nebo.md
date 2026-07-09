# Nebo / MyScript Notes (.nebo) format

Status: **container mapped; ink codec (BINK) not yet decoded**. Verified
against a MyScript Notes (Mac) export, 2026-07-09. Prior research called
.nebo "un-reversed" — the container at least is a plain zip.

## Container `[verified]`

```
rel.json                  {"pages": {...}} page relationships
index.bdom                magic "BDOM" v2: document object model, binary
meta.json                 app/version metadata
pages/<id>/ink.bink       magic "BINK" v5: THE INK (4.5KB for 3 strokes)
pages/<id>/page.bdom      per-page BDOM (readable ASCII fragments:
                          "border...", css-ish tokens)
pages/<id>/meta.json      page metadata
pages/<id>/style.css      plain CSS (".smartpen {...}")
```

`BINK` header: `42 49 4e 4b 00 05 00 00 00 00 01 00 00 00 04 00` —
"BINK\0" + u32-ish version 5, then section counts `[unknown]`. This is
the RE target: it should hold centerline ink + dynamics (MyScript's
recognition engine needs raw input).

## SVG export lane `[verified]`

MyScript Notes exports SVG with `viewBox="0 0 210 297"` (A4 in
**millimeters**) where each stroke is a **filled outline polygon**
(`<path d="M ... L ... ">` looping back on itself) — variable width baked
into geometry, no centerline/pressure. Usable for display ingestion,
lossy for ink interchange. JIIX (JSON ink with semantics) exists only in
the MyScript SDK, not the app.

## Open questions

1. BINK codec: stroke framing, point encoding, pressure/timestamps.
   Known-shape corpus + the tiny file size make this tractable.
2. BDOM: layout tree; needed for page size/positioning.
3. Does the iPad Nebo app produce identical containers? (parity case 18.)

## Changelog

- 2026-07-09: container inventory from a controlled Mac export; SVG
  export characterized (outline polygons, mm units).
