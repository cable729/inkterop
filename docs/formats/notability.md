# Notability formats (.note legacy zip, .ntb modern export)

Status: **legacy format decoded and confirmed alive** on a 2026-era public
sample; **modern .ntb export decoded** (geometry, colors, tools, widths)
against a self-generated sample from Mac app 16.5.3 (2026-07-09), validated
by rendering against the app's own thumbnail. The .ntb `noteBundle` uses
the same FlatBuffers op-log encoding as the app's local persistence blobs,
so decoding one decoded both.

Confidence markers as in `docs/formats/goodnotes.md`.

## Prior art

Layout first documented by Julia Evans (jvns.ca, 2018-03-31,
"Reverse engineering the Notability file format"); write support was
demonstrated by her svg2notability. Verified here independently against a
public sample (294 strokes / 18 099 points): every documented invariant
held exactly.

## Container `[verified]`

A `.note` export is a ZIP with one top-level folder per document:

```
<Name>/Session.plist       ALL ink + session state (NSKeyedArchiver)
<Name>/metadata.plist      document metadata
<Name>/PDFs/<uuid>.pdf     imported PDF backgrounds
<Name>/NBPDFIndex/…        PDF text/layout indexes
<Name>/HandwritingIndex/…  recognition index
<Name>/thumb*.png          previews
```

Detection: zip containing a member ending in `Session.plist`
(distinguishes it from Supernote's binary `.note`, which starts with
`noteSN_FILE_VER_`).

## Session.plist `[verified]`

Apple **binary plist** wrapping an NSKeyedArchiver archive with
`$archiver = "GLKeyedArchiver"` (Ginger Labs subclass; plistlib +
UID-resolution reads it fine — no Foundation needed). Relevant classes
observed: `NoteTakingSession`, `HandwritingObject`, `InkedSpatialHash`,
`NBAttributedString`, `PDFFile`, `NBCPEventManager`.

Ink lives in objects carrying these keys (the `HandwritingObject`):

| Key | Encoding | Meaning | Confidence |
|---|---|---|---|
| `numcurves` | int | stroke count N | `[verified]` |
| `numpoints` | int | total point count | `[verified]` |
| `curvespoints` | bytes | float32 (x, y) pairs, all strokes concatenated | `[verified]` |
| `curvesnumpoints` | bytes | int32 × N: points per stroke (sums to `numpoints`) | `[verified]` |
| `curveswidth` | bytes | float32 × N: nominal stroke width | `[verified]` |
| `curvescolors` | bytes | 4 bytes RGBA × N (alpha < 255 ⇒ translucent marker/highlighter) | `[verified]` |
| `curvesfractionalwidths` | bytes | float32 per-point width fractions — **but** `numfractionalwidths ≠ numpoints` (6 229 vs 18 099 in the sample): the fraction→stroke mapping is **`[unknown]`** | partially |
| `numfractionalwidths` | int | length of the above | `[verified]` |
| `eventTokens` | bytes | `[unknown]` |

The fractional-width mismatch is the main open question: fractions likely
apply only to pressure-drawn strokes, with the count per stroke stored
somewhere not yet identified. Until resolved, our reader renders strokes
at their constant `curveswidth` (which matches how several community
tools render Notability ink) and preserves the raw blob for research.

## Coordinates `[inferred]`

Float32 points, y down. Values are consistent with PDF points; Notability
documents are a continuous vertical scroll rather than fixed pages, so our
reader emits a single page sized to the ink extents (US-Letter width
floor). Page/paper settings likely live elsewhere in the session object —
not yet mapped.

## Cloud-era storage

The Mac app's working store is NOT the legacy zip. Container
(`~/Library/Containers/com.gingerlabs.Notability/Data/Library/Application
Support/local-persistence-collab-production/`):

- `local_persistence` — SQLite (GRDB): op-based sync tables
  (`op_buffer`, `ops_bundle_cache`, `cloudkit_note_edit_journal_entries`,
  `cloudkit_note_versions`, `note_metadata`, `organizers`, …)
  `[verified table list]`.
- `notes/<UUID>` — one FlatBuffers blob per note `[verified]`: root is
  `{field_0: vector<op table>, field_1: u16 = 12}` — the **same op
  tables, byte-identical structure**, as the `.ntb` `noteBundle` below
  (which wraps the op vector in a note-identity envelope). Confirmed by
  walking the container blob of the same note that produced the `.ntb`
  sample.

## .ntb (modern export, Mac app 16.x)

Decoded 2026-07-09 against a self-generated sample (app 16.5.3, macOS 15;
`core/tests/fixtures/notability/scribbles.ntb`, CC0: one black
fountain-pen scribble, two black pencil scribbles, one yellow highlighter
zigzag). Exploration tool: `tools/re/fbwalk.py` (schema-less FlatBuffers
walker, written from the published FlatBuffers internals doc only).
Rendering validation: stroke render overlaid on the export's own
`thumbnail.png` — geometry, per-stroke extents, colors, and highlighter
translucency all match; all four point blobs in the sample parse with
**zero residual bytes**.

### Container `[verified]`

A `.ntb` is a stored zip (no encryption):

```
version         ASCII "1"
manifest.json   {"appVersion": "16.5.3"}
noteBundle      FlatBuffers op log + note identity (the actual document)
thumbnail.png   page render (the app's own rasterization)
```

Detection: zip containing a member named `noteBundle`.

### noteBundle root table

FlatBuffers, little-endian; root uoffset at byte 0. Field indices are
vtable slots as reported by `fbwalk.py`.

| Field | Type | Meaning | Confidence |
|---|---|---|---|
| 0 | 16 bytes | opaque; hash/key? | `[unknown]` |
| 3 | string | note UUID, uppercase | `[verified]` |
| 4 | u64 (+4B pad) | note created, Unix epoch ms | `[inferred]` |
| 5 | string | note UUID, lowercase | `[verified]` |
| 6 | vector\<table\> | op log (see below) | `[verified]` |
| 7 | u16 = 12 | schema/protocol version? (container blobs carry the same 12) | `[unknown]` |

### Op tables

One table per edit operation, oldest first. Observed op envelope:

| Field | Type | Meaning | Confidence |
|---|---|---|---|
| 0 | 8–10B struct | (u32 = 0, u32 sequence: 0, 1, 3, 5, 7, 9 in the sample) | `[inferred]` |
| 1 | u64 ms | op timestamp | `[inferred]` |
| 2 | u64 ms | stroke ops: pen-up time | `[inferred]` |
| 3 | u64 ms | stroke ops: pen-down time | `[inferred]` |
| 4 | u8 | op type: 1 = document metadata, 3 = ?, 15 = add-stroke | `[inferred]` |
| 5 | table | payload, layout depends on op type | `[verified]` |

Op type 1 payload (document metadata): `field_0.field_0` = title string;
`field_1.field_0` = page attrs table with `field_3` = 2×f32 page size
(612, 792 = US Letter pt) and `field_4` = 4×f32 (36, 36, 36, 36;
margins?) `[inferred]`, plus a small table of u8s (paper style?)
`[unknown]`; `field_2.field_0` = "en_US"; `field_4` = font name
("Inter"), `field_5` = f32 font size (14) `[inferred]`.

Op type 3 payload: single u32 = 2 `[unknown]`.

### Stroke payload (op type 15)

| Field | Type | Meaning | Confidence |
|---|---|---|---|
| 0 | 12B struct | (0, 1, 0) — page/layer ref? | `[unknown]` |
| 1 | 2×f32 | stroke origin, page pt = the first anchor point | `[verified]` |
| 4 | u8 | tool: 0/absent = pen, 1 = pencil, 2 = highlighter | `[inferred]` |
| 5 | u8 = 1 | ? (absent on the pen stroke) | `[unknown]` |
| 7 | 4 bytes | color R,G,B,A (B and A pinned by the yellow alpha-107 sample; R-before-G order confirmed by the red calibration stroke, 2026-07-10 — decodes as (0.93,0.21,0.14)) | `[verified]` |
| 8 | f32 | base stroke width, pt (3.1875 pen/pencil, 15.9375 highlighter) | `[verified]` |
| 9 | vector\<u8\> | point blob (below) | `[verified]` |
| 14 | u32 = 999999 | highlighter only | `[unknown]` |
| 15 | u32 | 767/1194/1477/750 in the sample; raw input event count? | `[unknown]` |

### Point blob `[verified framing; Bézier semantics inferred]`

Strokes are stored as **fitted cubic Bézier chains**, coordinates
relative to the stroke origin (y down, pt):

```
u8   coord_fmt      0 = coords are f16, 1 = coords are f32
u16  point_count    number of anchors = segment records + 1
u8   = 3            [unknown; constant]
u32  = 0            [unknown; constant]
(fmt 1 only) u32 = 0   [unknown; constant]
then per segment record:
  f16  width multiplier at this anchor (× payload field_8 base width)
  f16  = 1.0            [unknown; constant in sample]
  u8 = 0xff, u16 = 0    [unknown; constant]
  3 × (x, y)            control1, control2, end of one cubic segment,
                        f16 or f32 per coord_fmt
tail (6 bytes):
  f16 width multiplier at the final anchor, f16 = 1.0, 0xff, 0x00
```

The chain starts at (0, 0) relative (= the origin field). Blob length =
8 (+4 if fmt 1) + records×(7 + 6×coordsize) + 6 exactly, on all four
sample strokes. Evidence for "cubic Bézier" over "flat polyline
triples": each segment's control1 mirrors the previous segment's
control2 about the shared anchor to a median error of 2–9% of the
tangent length (C1 continuity — the signature of a smoothing curve
fitter) `[inferred]`. The f16 relative coordinates are why strokes carry
an f32 origin: precision stays sub-0.25pt for strokes up to ~500pt
across. The highlighter stroke in the sample uses fmt 1 (f32); whether
fmt selection follows tool or stroke size is `[unknown]`.

The width-multiplier channel is a per-anchor pressure/width profile: the
fountain-pen stroke decays 1.27 → 0.38 while pencil and highlighter hold
1.0 `[inferred]`.

### .ntb reader

`core/src/inkterop/formats/notability/ntb.py` — strokes (Bézier chains
flattened at 4 samples/segment), per-point widths, colors, tool mapping,
title, page size, created timestamp. Not yet mapped: text objects, PDF
backgrounds, images, audio, multi-page/section behavior.

### .ntb writer (experimental, `validated=False`)

`core/src/inkterop/formats/notability/writer.py` +
`formats/notability/fb.py` (a minimal hand-rolled FlatBuffers builder,
same internals-doc-only provenance as `fbwalk.py`). The writer is the
exact inverse of the reader: it emits only the tables/slots the reader
consumes and mirrors the `scribbles.ntb` fixture byte patterns for
everything else — container member list/order (stored zip: `version`,
`noteBundle`, `manifest.json` with appVersion 16.5.3, white placeholder
`thumbnail.png`), root constants (u16 = 12; opaque 16 bytes written as
zeros `[unknown]`), the type-1 metadata op (page size from page-0
bounds, 36 pt margins, `en_US`, Inter/14), the type-3 op (`u32 = 2`),
and op envelopes with sequence numbers 0, 1, then odd-ascending
type-15 stroke ops.

Strokes: IR polylines become coord-fmt-1 (f32) point blobs with one
*exactly linear* cubic per segment (controls at 1/3 and 2/3 of the
chord), so the reader's uniform flattening reproduces the written
polyline verbatim; per-anchor pressure profiles ride the f16 width
multipliers (`exact` fidelity). `native` writes multipliers 1.0 at the
app's observed default base widths; `raw` raises. Multi-page documents
write page 1 only with a warning (op-log page framing `[unknown]`).

~~Validation gate: color byte order R vs G~~ **RESOLVED 2026-07-10**:
the red calibration stroke (corpus/calibration/notability-calibration
.ntb) decodes as red with the reader's byte order — the gate is now
only the usual app-open check per `docs/validated-writes.md`. Precedent
note: svg2notability demonstrated third-party *writes* Notability
accepts, but against the **legacy** `Session.plist` format — if .ntb
app-import fails, a legacy plist writer is the fallback lane.

## Version detection `[verified on one sample each]`

- Legacy `.note`: zip + `Session.plist` + `$archiver ==
  "GLKeyedArchiver"` + `curvespoints` present.
- Modern `.ntb`: zip + a `noteBundle` member (the Mac app 16.x
  "Save as…" export; it no longer emits the legacy zip).
- Anything else: the legacy reader raises "new cloud-era Notability
  format?".

## Legacy reader

`core/src/inkterop/formats/notability/reader.py` — strokes, colors,
per-stroke widths, highlighter inference from alpha. PDF backgrounds,
text, audio not yet mapped.

## Open questions

Legacy `.note`:

1. `curvesfractionalwidths` → stroke mapping (pressure profile!). Corpus
   case 16 (pressure ramp) is designed to crack this.
2. Page/paper metadata location; PDF-background ↔ ink alignment.
3. `eventTokens`, `InkedSpatialHash` internals (probably derived data).

Modern `.ntb`:

4. Color byte order R vs G (needs a red/blue corpus case); full tool
   enum beyond {pen, pencil, highlighter} (ballpoint? brush? eraser
   effects on the op log?).
5. The constant fields: point-blob header u8 = 3, per-record f16 = 1.0
   and 0xff/0x0000, fmt-1 extra u32; stroke payload field_15 (input
   event count?), field_14 = 999999 (highlighter only), field_5;
   op type 3.
6. Editing semantics of the op log: what erase/move/undo ops look like
   (the sample only contains create + add-stroke ops), and whether ops
   ever supersede earlier ones — matters before trusting "read every
   type-15 op" on edited notes.
7. Text objects, PDF backgrounds, images, audio in .ntb; multi-page /
   section notes.
8. Write support: an experimental writer exists (see ".ntb writer"
   above) but stays `validated=False` until corpus cases for #4–#6 and
   an app-open check land (validated-writes policy applies).

## Changelog

- 2026-07-09: independent verification of the 2018 legacy spec on a 2026
  public sample; legacy reader implemented; fractional-width mismatch
  documented.
- 2026-07-09 (later): modern `.ntb` (app 16.5.3) decoded — FlatBuffers
  op log, Bézier-chain point blobs, per-anchor width multipliers;
  `NtbReader` implemented; render validated against the app's own
  thumbnail; container `notes/<UUID>` blobs confirmed to share the op
  encoding.
- 2026-07-09 (later still): experimental `.ntb` writer (`NtbWriter`,
  `validated=False`) + hand-rolled FlatBuffers builder; write→read
  round-trips (synthetic + fixture) and fbwalk framing checks in
  `core/tests/test_ntb_writer.py`; validation gated on the color
  byte-order corpus case (open question #4) and an app-open check.
