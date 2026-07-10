# Onyx Boox Notes (.note) format

Status: **read support for ink, text boxes, and geometric shapes**
(`formats/boox.py`). Verified against the two Note Air 5c samples shipped
with the MIT-licensed [boox-note-optimizer](https://github.com/nrontsis/boox-note-optimizer)
(corpus: `corpus/third-party/boox-note-optimizer/web/{demo,empty}.note`,
firmware as of 2026-02). Format facts are an independent Python
implementation cross-checked between that project's format documentation
(MIT — readable, unlike goodparse) and our own byte-level probing of the
samples; the width formulas are their empirical fits against
device-exported PDFs, which we have **not** re-validated ourselves.

`.note` is a three-way extension collision: Supernote (binary
`noteSN_FILE_VER_`), Notability (zip + `Session.plist`), and Boox (zip +
`<noteId>/note/pb/note_info`). The registry disambiguates via `detect()`.

**Out of scope for now:** the older device-*backup* format (SQLite
`ShapeDatabase.db`, `NewShapeModel` table, byteswapped Nx6 f32 points in
normalized 0-1 coordinates — see RobertCsordas/OnyxNoteRenderer, BSD-3)
is a different container entirely and is deferred.

## Container `[verified]`

Zip archive. Single-note archives root everything at `<noteId>/`:

| Member | Content | Confidence |
|---|---|---|
| `note/pb/note_info` | protobuf: doc metadata (wrapped at field 1) | `[verified]` |
| `pageModel/pb/<uuid>` | protobuf: repeated per-page entries | `[verified]` |
| `point/<pageId>/<pageId>#<pointsDocId>#points` | binary stroke points | `[verified]` |
| `shape/<pageId>#<shapeDocId>#<ts>.zip` | nested zip → protobuf stroke metadata | `[verified]` |
| `virtual/{doc,page}/pb/*` | template/zoom bookkeeping (unused by reader) | `[verified present]` |
| `template/json/<pageId>.template_json` | page template JSON (unused) | `[verified present]` |
| `resource/pb/<uuid>#<ts>` | embedded resources, e.g. background images (unused) | `[inferred]` |
| `extra/pb/extra` | small counters (unused) | `[unknown]` |
| `stash/**` | undo history — ignored (≈46% of file size) | `[verified]` |

Multi-note archives put a root `note_tree` protobuf (repeated note
metadata at field 1) above the same per-note trees `[inferred, untested —
no sample]`.

Strokes are cross-referenced by UUID: the `#points` index entry UUID ==
shape protobuf field 1.

## `#points` blob `[verified]`

All integers **big-endian** (unusual — the protobuf fixed32s elsewhere
are standard little-endian).

```
header (76B): u32 (always 1 observed) + 36B ascii pageId (condensed,
              space-padded) + 36B ascii pointsDocId (hyphenated)
per stroke:   4B zero pad + N x 16B point records
index:        44B entries: 36B ascii shapeUUID + u32 offset (absolute)
              + u32 size (pad + points)
last 4B:      u32 absolute offset of the index start
```

Point record `>ffBBHI`:

| Offset | Field | Notes | Confidence |
|---|---|---|---|
| 0 | f32 x | PDF points, 1:1 with device PDF export | `[verified upstream]` |
| 4 | f32 y | 0 at top, grows down | `[verified upstream]` |
| 8 | u8 tilt_x | azimuth, 256 units/turn, wraps | `[inferred]` |
| 9 | u8 tilt_y | elevation, unknown units (15-33 observed) | `[unknown]` |
| 10 | u16 pressure | 0-4095 (`maxPressure` in pen config) | `[verified]` |
| 12 | u32 t | ms since stroke start, **cumulative** | `[verified]` |

The upstream README calls the timestamp a per-point delta; empirically
(all 9 demo.note strokes) it is monotonically non-decreasing and starts
at 0 — cumulative, matching their code, not their README.

Page size 1860x2480 points on Note Air 5c (`note_info` fields 22/23 and
`pageInfoMap`); `point_scale = 1.0`.

## Shape protobuf `[verified fields]`

Nested zip member = repeated field-1 submessages, one per stroke:

| Field | Type | Content |
|---|---|---|
| 1 | string | shapeUUID (joins `#points` index) |
| 2, 3 | varint | created/modified epoch-ms (z-order key) |
| 4 | varint | ARGB color, sign-extended int64 (mask to u32) |
| 5 | fixed32 LE | thickness (PDF points) |
| 6 | varint | layer id → `pageInfoMap[pid].layerList` |
| 7 | string | bbox JSON `{left,top,right,bottom,...}` |
| 8 | string | 3x3 affine JSON `{"values":[a,b,tx,c,d,ty,0,0,1]}` (or bare array) |
| 9 | string | text style JSON (`textSize`, `alignType`, ...) |
| 10 | string | plain text (text boxes) |
| 11 | string | pen config JSON (`maxPressure`, `displayScale`, `dpi`, ...) |
| 12 | varint | pen type (below) |
| 16 | string | pointsDocId |
| 17 | string | line style JSON |
| 18 | string | shapeDocId |
| 20 | string | GeoJSON `featureCollection` (pen 40) |
| 22 | string | rich text HTML |
| 23 | varint | fill color ARGB `[inferred]` |
| 25 | bytes | legacy shape point list: 4B header + 16B records, x/y f32 BE `[inferred]` |

## note_info metadata `[verified fields]`

Field 1 wraps the metadata message (the reader unwraps when *all*
top-level fields are field 1, which also covers `note_tree`):
1 noteId, 6 title, 12 canvas-state JSON (`defaultPageRect`,
`pageInfoMap` with per-page `width`/`height`/`layerList`), 13 background
JSON, 14 device JSON, 20 `pageNameList` JSON (page order), 22/23 canvas
w/h fixed32 LE. pageModel entries: 1 pageUUID, 2 layerList JSON,
7 dims JSON.

## Pen types and IR mapping

| pen | Boox tool | IR family | Geometry | Confidence |
|---|---|---|---|---|
| 2 | ballpoint/fineliner | BALLPOINT | constant width = thickness | `[verified upstream]` |
| 5 | fountain | PEN | `w = th*1.37*(p/4095)^0.59` variable | `[inferred]` fit |
| 6, 16 | text box | → `TextBlock` (field 10 / stripped field 22) | | `[inferred]` |
| 15 | highlighter | HIGHLIGHTER | constant width, ~50% multiply, underlay | `[inferred]` |
| 21 | marker | MARKER | `w = th*2.35*(p/4095)^0.43` | `[inferred]` fit |
| 22 | charcoal | SHADER | fountain-width envelope; device grain texture NOT reproduced | `[inferred]` approx |
| 37 | fill | UNKNOWN | scanline pairs kept as one polyline | `[unknown]` approx, no sample |
| 40 | geometric shapes | PEN | GeoJSON field 20: LineString/DirectionLine/Polygon/MultiLineString/Oval/Curve sampled to polylines; Arc/Bracket/WaveLine skipped with a warning | `[inferred]`, no sample |
| 60, 61 | calligraphy | CALLIGRAPHY | device fills chisel-tip polygons; approximated as variable-width stroke | `[inferred]` approx |
| 0/1/7/8/…/31 | legacy shapes (field 25 point list) | PEN | oval/rect/line/polygon polylines | `[inferred]`, no sample |

Width min-clamp 0.5pt; a shape transform (field 8) is applied to points
and scales thickness by the average axis scale. Widths beyond min-clamp
are already in PDF points — never re-derive from pressure when consuming
the WIDTH channel.

Channels: PRESSURE (p/maxPressure), TIMESTAMP (t/1000 s),
TILT_AZIMUTH (`tilt_x * 2π/256` `[inferred]`). Raw `tilt_y` (unknown
units) rides in `stroke.extra["boox"]["tilt_y"]`. Strokes with points
but no shape metadata are kept with default style, family UNKNOWN.

Z-order: layers in `layerList` order, strokes by created timestamp
within a layer, orphans in a trailing layer `[inferred]`.

## Not mapped (yet)

- Page templates (`template_json` — the template SVGs live on Boox's
  CDN, not in the file) and background images (`resource/pb`).
- Charcoal grain texture (device rasterizes per stroke; algorithm
  unknown upstream too).
- Rich-text formatting (HTML is flattened to plain text).
- The SQLite backup `.note` format (see above).

## Open questions

0. Erase representation: `stash/` (undo history) is ignored, but the
   reader keeps points that lack a shape record as orphan default-style
   ink — if erasing removes the shape but leaves the `#points` entry,
   erased strokes would resurrect. Needs an erased sample
   (`docs/erase-audit.md`).
1. `#points` header u32 — version or page count? Only 1 observed.
2. `tilt_y` semantics/units (elevation 15-33 observed on charcoal).
3. Pen-type completeness — only 2/5/15/21/22/60/61 observed in samples;
   37/40/6/16 and the legacy field-25 shapes are implemented from
   upstream docs alone.
4. Firmware variation: boox-note-parser (Note Air 4C) reports different
   field 11 content and doesn't see fields 4/12 — firmware-dependent?
5. Multi-note `note_tree` archives — no sample; unwrapping logic is a
   best guess.

## Changelog

- 2026-07-09: initial spec + reader; verified against
  boox-note-optimizer's demo/empty samples (9 strokes, 8179 points);
  synthetic CC0 fixture (`core/tests/fixtures/boox/`).
