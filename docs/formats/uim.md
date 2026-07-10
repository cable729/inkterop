# Wacom Universal Ink Model (.uim) format

Status: **read support (UIM v3.0.0 and v3.1.0) + write support (v3.1.0)**.
Independent stdlib-only implementation; format facts and protobuf field
numbers derived from Wacom's Apache-2.0 reference implementation
([universal-ink-library](https://github.com/Wacom-Developer/universal-ink-library),
`uim/codec/parser/` + generated schema in `uim/codec/format/`) and the
public spec at developer-docs.wacom.com. Reader verified against all 11
corpus samples (5x v3.0.0, 6x v3.1.0) and oracle-tested numerically
against the Wacom library's own parse (`tests/test_uim.py`). Writer
(`encode_uim`, 2026-07-09) oracle-tested both directions: everything it
emits parses in Wacom's own library, and re-encoding every corpus file
round-trips with identical stroke/point counts and xy deltas within the
float32 quantum (`tests/test_uim_writer.py`). `.will` (WILL-2) is a
different container and NOT handled.

## Container `[verified]`

RIFF (little-endian), form type `UINK`:

```
"RIFF" <u32 size> "UINK"
  "HEAD" <u32 size> <major u8> <minor u8> <patch u8> ...
  ... chunks, each padded to even size
```

- **v3.0.0**: HEAD is just the 3 version bytes (+ pad). A single `DATA`
  chunk follows containing one protobuf `InkObject` message with
  everything inside.
- **v3.1.0**: HEAD continues with 1 reserved byte + one 8-byte
  description per following chunk: `major, minor, patch, content-type,
  compression, 3 reserved`. Content types: 0 binary, 1 protobuf, 2 JSON,
  3 text. Compression: 0 none, 1 zip, 2 LZMA. All corpus chunks are
  protobuf/uncompressed; compressed chunks are `[unknown]` (the Wacom
  library refuses them; we attempt zlib/lzma and skip on failure).
  Chunks: `PRPS` properties, `INPT` input/sensor data, `BRSH` brushes,
  `INKD` ink data, `KNWG` knowledge graph, `INKS` ink structure.

## Message map (field numbers) `[verified]`

Mirrored from the Wacom library's generated schema; only what the reader
consumes is listed (full maps in `core/src/inkterop/formats/uim.py`).

v3.1.0 `InkData`: 1 strokes, 2 unitScaleFactor, 3 transform (Matrix,
m00..m33 = fields 1..16), 4 brushURIs, 5 renderModeURIs,
6 properties (PathPointProperties table).

v3.1.0 `Stroke`: 1 id (16-byte uuid), 2 precisions (sint32),
3/4 start/endParameter, 5 splineData, 6 splineCompressed,
7 propertiesIndex (1-based into InkData.properties) / 8 propertiesValue,
9 brushURIIndex / 10 brushURIValue, 11/12 renderModeURI idx/value,
14 sensorDataOffset, 15 sensorDataID, 16 sensorDataMapping.
`SplineData`: 1 splineX, 2 splineY, 3 splineZ (floats), 4-7 r/g/b/a
(uint 0-255), 8 size, 9 rotation, 10-15 scale/offset xyz.
`SplineCompressed`: same numbers, sint32 zigzag **deltas** scaled by
10^precision; per-purpose precisions packed in 4-bit fields of
`Stroke.precisions` (position bits 0-3, size 4-7, rotation 8-11,
scale 12-15, offset 16-19). `PathPointProperties`: 1 color (zigzag
int, RGBA with R in the high byte), 2 size.

v3.0.0 `InkObject`: 1 inputData, 2 inkData, 3 brushes, 4 inkTree,
5 views, 6 knowledgeGraph, 7 transform, 8 properties. `Stroke` (flat):
1 id (uuid string), 4/5/6 splineX/Y/Z (plain floats), 7-10 r/g/b/a
(floats 0-1), 11 size, 19 sensorDataOffset, 20 sensorDataID, 22 style
(1 properties → Float32-wrapped size/red/green/blue/alpha…, 2 brushURI,
4 renderModeURI).

`InputData` (both versions): 1 inputContextData (5 sensorContexts →
2 sensorChannelsContext → 2 channels), 2 sensorData. `SensorChannel`:
1 id, 2 type URI (`will://input/3.0/channel/<X|Y|Z|Timestamp|Pressure|
Azimuth|Altitude|Rotation|RadiusX|RadiusY>`), 3 metric, 4 resolution
(double), 5/6 min/max, 7 precision. `SensorData`: 1 id, 2 inputContextID,
4 timestamp (uint64 epoch ms), 5 dataChannels (1 sensorChannelID,
2 values: zigzag deltas). Decoded value =
`cumsum(delta) / (resolution * 10^precision)`; Timestamp channels
additionally seed the cumulative sum with the record timestamp
(`ts_ms / (resolution * 10^precision)` — with resolution 1000 that
yields seconds since epoch). Channel ids are unique, so the
environment/device/context indirection can be flattened.

## Geometry `[verified]` / flattening `[inferred]`

Strokes store Catmull-Rom spline control points; control points lie on
the curve, and the first/last are duplicated phantom endpoints. We drop a
phantom endpoint only when it exactly equals its neighbor (mirrors the
Wacom library's `remove_duplicates_at_ends`) and emit the control polygon
as the IR polyline — a piecewise-linear flattening that skips the
curve interpolation `[inferred: adequate at real sampling densities]`.

Spline point → sensor sample alignment mirrors the library's
`get_sensor_point`: index shifted down by one when `sensorDataOffset == 0`
(compensating the phantom start point), explicit `sensorDataMapping`
when present, clamped at the end.

Cross-check: the corpus 3.0 files and their 3.1 "delta" re-encodings
decode to identical stroke/point counts and geometry within the 10^-2
compression quantum `[verified]`.

## IR mapping

- One page, one layer, strokes in InkData order (ink-tree grouping not
  read). Bounds = ink extent unioned with the origin; `point_scale =
  72/96 = 0.75` `[inferred]`: spline units are WILL DIPs (1/96 in) — the
  corpus X/Y sensor channels declare resolution 3779.5275590592/m =
  exactly 96 dpi.
- v3.1 `InkData.transform` (2D affine part) is applied to points, with
  `unitScaleFactor` as a fallback scale; no transformed sample exists
  `[inferred]`. v3.0 `InkObject.transform` likewise.
- Channels: per-point `size` → WIDTH (appearance STROKED_VARIABLE when it
  varies, STROKED_CONSTANT otherwise); Pressure → PRESSURE normalized by
  the channel's min/max `[inferred: corpus channels already declare
  0..1]`; Azimuth/Altitude → TILT_* (radians pass-through; axis
  conventions vs our "0 = +x CCW" contract unverified `[unknown]`);
  Timestamp → TIMESTAMP as seconds since stroke start.
- Color: constant style RGBA (v3.1 packed int, v3.0 floats); alpha →
  appearance opacity. Per-point r/g/b collapse to the first value
  `[inferred]`; per-point alpha becomes the ALPHA channel when varying.
- Tools: UIM has brushes, not semantic tools. Brush URI containing
  "highlight" → HIGHLIGHTER (multiply blend + underlay), other vector
  brushes → PEN, raster (particle) brushes → UNKNOWN; the URI and render
  mode ride along in NativeTool params.

## Writer (`encode_uim`) and the IR↔UIM feature-fit matrix

`encode_uim(doc, page_index)` emits one IR page as a RIFF UINK v3.1.0
file (chunks PRPS/INPT/BRSH/INKD/INKS — INKS is not optional: Wacom's
`InkModel.strokes` only walks the ink tree, so a file without it parses
but appears empty). Output is byte-deterministic (uuid5 stroke ids,
`SensorData.timestamp = 0`). Coordinates are DIPs:
`dip = source_units × page.point_scale / 0.75`, one factor applied to
x, y, the WIDTH channel and `appearance.width` alike. Strokes are all
layers in layer order, **including invisible layers** — the writer is a
container/interchange encoder, not a renderer, and must not drop content.

The matrix below is the evidence base for the interchange-format
decision ("does the IR translate cleanly into UIM"). Fit levels:
**1:1** (native slot) / **convention** (works via a documented
vocabulary, foreign readers may not honor it) / **properties-hack**
(rides in PRPS key/values) / **no-fit** (dropped; carried by the .inkz
overlay instead — see `docs/formats/inkz.md`).

| IR concept | Fit | Detail |
|---|---|---|
| Raw PRESSURE / TILT_AZIMUTH / TILT_ALTITUDE / TIMESTAMP | **1:1** | native sensor channels, zigzag deltas; quantization ≤ 5e-5 (precision 4) / 0.5 ms; Pressure declares min 0 / max 1 so read-back normalization is the identity |
| Raw SPEED | no-fit | no UIM channel URI; dropped (derivable from X/Y+TIMESTAMP) |
| Resolved WIDTH (per point) | **1:1** | `SplineData.size` incl. phantom endpoints; constant width → `PathPointProperties.size` |
| Resolved ALPHA (per point) | **1:1** | `SplineData.alpha` uint8 (1/255 quantization); stroke opacity → properties color alpha byte |
| Tool identity | convention | `inkterop://brush/<family>` brush vocabulary; exact round-trip through our reader; foreign readers only get the "highlight" substring heuristic; same-format brush URIs pass through verbatim from `NativeTool` |
| Nib shape | no-fit (today) | writer emits a fixed Circle `BrushPrototype`; UIM *can* model polygon nibs — revisit when a measured rendering rule needs it |
| Texture / particle look | no-fit (today) | raster (particle) brushes unused; the IR has no texture concept yet either — the gap is mutual |
| Blend / underlay | convention | not encoded; reconstructed on read from the highlighter family (multiply + underlay) |
| Native payload (foreign) | properties-hack | PRPS `inkterop.native.<stroke-id>` = base64 JSON of NativeTool+extra, capped at 2 KiB; not consumed on read (the .inkz overlay is the real carrier) |
| Backgrounds (template/PDF/image/color) | no-fit | UIM has no page concept — this is the .inkz manifest's job |
| Typed text blocks | no-fit | dropped (KNWG holds *recognized* text, not typed layout) |
| Multi-page | convention | one UIM file per page (`out.uim`, `out-2.uim`, …); page index/count in the `inkterop.doc` PRPS entry |
| point_scale / bounds | properties-hack | geometry lands in DIPs; the reader re-derives bounds from ink extent at fixed 0.75; true bounds/point_scale recorded in `inkterop.doc` but not consumed (the .inkz manifest is authoritative) |
| Layers | no-fit | flattened in layer order (INKS tree kept flat); layer structure reconstructed by the .inkz manifest |

**Verdict for the interchange decision (2026-07-09)**: the *stroke-level*
model fits well — channels, resolved width/alpha and geometry are 1:1
with only float32/uint8 quantization; tool identity works by vocabulary.
Everything document-level (pages, backgrounds, text, layers) has no UIM
home, which is exactly the wrapper role the `.inkz` container fills. The
two expressiveness gaps UIM was expected to close (nib shape, texture)
are unused so far because the IR itself can't express them yet — they
stay open until a measured per-app rendering rule demands them.

Encoding notes worth keeping: proto3 zero-defaults make a properties
color of exactly 0x00000000 wire-indistinguishable from "absent"; the
writer force-emits the field to match the reader's presence check. The
writer emits `appearance.color` (render color); a source whose semantic
color differs loses the distinction through UIM alone (the .inkz overlay
preserves it).

## Open questions

1. `KNWG` semantic triples (handwriting recognition text, entities) —
   could populate IR TextBlocks; skipped for now.
2. `INKS` ink tree/views — grouping/z-order semantics beyond flat
   InkData order; also stroke fragments (`Interval` nodes).
3. Chunk compression (zip/LZMA) and JSON content type — no samples;
   best-effort stdlib decompress `[unknown]`.
4. Raster brush rendering (particle scattering, textures) — appearance
   is approximated as a round stroked polyline.
5. Azimuth/altitude axis conventions vs IR contract.
6. Catmull-Rom subdivision for `exact` fidelity rendering (currently
   control-polygon flattening).

## Changelog

- 2026-07-09: initial reader (3.0.0 + 3.1.0), fixture generated with the
  Wacom reference encoder, oracle tests vs the Wacom parser.
- 2026-07-09 (later): v3.1 writer (`encode_uim` + `UimWriter`,
  `validated=True` under the open-format exception), oracle-tested both
  directions against the Wacom library; reader's `_tool_family` now maps
  the `inkterop://brush/<family>` vocabulary back to exact families;
  feature-fit matrix added (interchange-decision evidence).
