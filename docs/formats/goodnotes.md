# GoodNotes (.goodnotes) format

Status: **ink strokes + color + pen-type field decoded across both
schema versions; schema-25 journal structure + events-log page model
decoded; same-format round-trip imports and renders in GoodNotes Mac
(2026-07-09 round 3)**. Verified against public GoodNotes 6 samples
(schema 24) AND a controlled Mac-app export (GoodNotes 6, Mac App Store,
2026-07-09, schema 25 — committed as
`core/tests/fixtures/goodnotes/gn-mac-mixed-pens.goodnotes`). Open: pen-type
*names*, erasers, images, text, page dims, shape geometry.

Confidence markers: `[verified]` = confirmed by decoding real files with
independent code and checking invariants; `[inferred]` = consistent with
all observed files, no isolating experiment yet; `[unknown]` = observed
bytes, meaning not established.

## Prior art & provenance

Container layout, LZ4 framing, and stroke-triplet encoding were first
publicly documented in the README of
[franzthiemann/goodparse](https://github.com/franzthiemann/goodparse)
(GPL-3.0). This repo (MIT) uses those **format facts** and public sample
files only; goodparse's source code was deliberately not read or reused.
The typed-section layout of the geometry blob (below) is our own finding
and goes beyond what goodparse documents. Divergences and new findings
should be reported back to goodparse as issues, not code.

## Container `[verified]`

A `.goodnotes` file is a plain ZIP (no encryption observed):

```
thumbnail.jpg            page-1 preview
notes/<UUID>             one file PER PAGE: stroke records (see below)
attachments/<UUID>       embedded PDFs (page backgrounds / imports)
index.notes.pb           document/page index (protobuf)
index.events.pb          event journal; holds the DOC UUID + PAGE LIST (below)
index.search.pb          [unknown] (handwriting search index?)
index.attachments.pb     attachment index (protobuf)
schema.pb                2 bytes: field 1 varint = schema version
                         (observed 24, 25) — NOT an embedded schema [verified]
document.info.pb         schema 25+: empty (0B) in observed exports [unknown]
search/<UUID>            schema 25+: tiny per-page blobs [unknown]
```

## Page files `notes/<UUID>` `[verified]`

A stream of length-delimited protobuf records: `<varint len><message>`.
The record STRUCTURE differs by schema version, and the app enforces it
at import (established 2026-07-09 by import bisection against GoodNotes
Mac — swapping single members into a real container until it broke):

**Schema 24** (public iPad-era samples) — flat: one metadata record
(fields `1` uuid, `2`, `3`, `8`/`9` varints, `16` = 24), then stroke
records (a single field `7` each).

**Schema 25** (Mac exports) — an event JOURNAL of strict
`(header, payload)` record PAIRS `[verified]`:

```
header  := {1: event-uuid (36 chars), 2: version-msg {1: seq, 2: nonce},
            8: device-id (u62, same value as index.events.pb),
            9: item index (unique small int), 14: session const, 16: 24}
payload := stroke record {7: stroke-msg}   (ink)
         | {9: page-item msg}              (shapes/lasso items: bbox
                                            floats + item uuid + color)
```

Pairing rules `[verified by import behavior]`: the payload's stroke
uuid (field 1) REPEATS the header's event uuid, and the stroke's field
15 is a byte-exact ECHO of the header's field-2 version msg. An
unpaired stroke record fails import with
`SwiftProtobuf.BinaryDecodingError error 2`; per-record version fields
stay **24** even in schema-25 files (only `schema.pb` and the journal
shape change).

## Stroke message (inside record field #7)

| Field | Type | Meaning | Confidence |
|---|---|---|---|
| 1 | string | stroke UUID (36 chars) | `[verified]` |
| 2 | bytes | geometry: Apple-framed LZ4 → tpl blob | `[verified]` |
| 3 | varint | observed 1 or 5 | `[unknown]` |
| 4 | message | color: float32 subfields 1=R 2=G 3=B 4=A; omitted subfield = 0.0 (black pen = only alpha present) | `[verified]` |
| 6 | bytes | often empty | `[unknown]` |
| **7** | message | **pen type**: subfield 1 is a message whose field 1 varint = pen-type id (absent ⇒ 0); its field 2 = large varint `[unknown]` | `[verified]` (2026-07-09 Mac corpus) |
| 9 | bytes | often empty | `[unknown]` |
| 14/15 | message | small varints | `[unknown]` |
| 20 | bytes | often empty | `[unknown]` |
| 21 | varint | schema version (24/25) | `[inferred]` |

### Pen-type ids (field 7 → sub 1 → field 1)

Observed on a page drawn with one stroke per tool: ids
`{0, 1, 2, 3, 4, 5, 7}`. Confirmed by behavior: **4 = highlighter**
(24 pt constant width, drawn as highlighter) `[verified]`; **7 = shape
tool** (empty inline geometry — shape geometry stored elsewhere,
`[unknown]` where) `[inferred]`; **3 = pencil** (11-float segment layout
with tilt defaults, see below) `[inferred]`. Ids 0/1/2/5 are pens whose
UI names await the labeled corpus (case 05): 0/2/5 store pressure
triplets (5 observed on a wide 18 pt brush-like stroke), 1 stores
constant-width segments (ball pen?).

## Geometry blob (after Apple-LZ4 decompression)

Apple `libcompression` framed LZ4 `[verified]`:
`bv41 <u32 decompressed_size> <u32 compressed_size> <LZ4 block>` …
terminated by `bv4$`; `bv4-` prefixes a raw (uncompressed) block.
Independent decoder: `core/src/inkterop/formats/goodnotes/wire.py`.

Decompressed layout `[verified against all public samples, no residual
bytes]` — this typed-section structure is our finding:

```
"tpl\0" <u32 total_length>
<ASCII type signature, NUL-terminated>
sections, in signature order
```

Signature grammar `[verified — parses every observed blob with zero
residual bytes]`:

```
sig    := token*
token  := scalar | "A(" elem ")"
scalar := "v"            # one u16
        | "u"            # one float32
        | "f"            # observed only with count 0; size [unknown]
elem   := scalar | "S(" scalar+ ")"     # struct of scalars
array  := u32 count + count elements
```

The signature varies **per pen type**; three families observed:

**Pressure pens** (types 0/2/5; schema 24 fountain pen):
`vA(v)A(u)A(u)A(v)A(v)A(u)A(u)A(u)A(u)A(v)` —

| # | Type | Content | Confidence |
|---|---|---|---|
| 1 | u16[] | small flags | `[unknown]` |
| 2 | f32[3-4] | (x, y, w[, 0]) anchor | `[inferred]` |
| 3 | f32[3n] or f32[9m] | **the rendered path** (see layouts below) | `[verified]` |
| 4 | u16[] | small values | `[unknown]` |
| 5 | u16[] | per-segment codes | `[unknown]` |
| 6 | f32[] | (x,y) pairs subset — knot points? | `[unknown]` |
| 7 | f32[] | often empty | `[unknown]` |
| 8 | f32[2m] | (x, y) polygon ≈ precomputed **outline polygon** | `[inferred]` |
| 9 | f32[5n] | x, y, w + two more per point — raw dynamics? | `[unknown]`, high value |
| 10 | u16[n] | per-point flags | `[unknown]` |

**Constant-width pens** (types 1/4/7):
`vuA(v)A(S(uu))A(S(uuuu))vA(f)` — the lone `u` scalar is the **pen width
in points** `[verified]` (1.56 pt ball pen, 24 pt highlighter);
`A(S(uu))` holds a single anchor pair; **`A(S(uuuu))` is the path** as
flattened segments (x1, y1, x2, y2), consecutive segments ~touching
`[verified]`. Shape strokes (type 7) have all counts 0.

**Pencil** (type 3):
`vuA(v)A(S(uuuuu))A(S(u*11))A(S(uu))A(v)A(S(uu))A(S(uuuu))A(u)` —
`A(S(u*11))` is the path as segments `(?, x1, y1, c3, c4, 0, x2, y2, c3,
c4, 0)` where col0 is non-float-like bits `[unknown]` and c3/c4 sit at
**pi/6 and pi/3 — Apple Pencil's default altitude/azimuth** on a Mac
(no physical tilt) `[inferred]`. Corpus case 17 (iPad tilt) should make
these vary → raw tilt for `--fidelity raw`.

### Path layouts within f32 arrays `[verified]`

- **Flat triplets**: count divisible by 3; (x, y, width) per point
  (fountain/pressure pens).
- **9-float segments** (wide brush strokes, schema 25): count divisible
  by 9; groups of (x1, y1, w1, x2, y2, w2, 0, 0, k) with k≈0.1 constant
  `[unknown]`; path = interleaved segment endpoints.
- Widths are device-rendered with pressure baked in (like reMarkable);
  never re-derive from pressure.

## Coordinates & units `[verified]`

PDF points @ 72 dpi, origin top-left, y down. Observed pages are A4
(595.28 × 841.89 pt); the page-dimension field is `[unknown]` — our reader
assumes A4 until the corpus isolates it (case 14). Width per point is the
**device-rendered width with pressure baked in** (thin pressure pens
~0.1–1.4 pt, thick pens ~3–4.5 pt) — same design as reMarkable Paper Pro's
`point.width/4`. Do not layer pressure formulas on top.

## index.notes.pb `[inferred]`

Length-delimited records; field 1 of each = UUID string. Observed to list
page UUIDs in document order; our reader uses it for page ordering and
falls back to zip order.

## Reader

`core/src/inkterop/formats/goodnotes/` — ink + color only, marked
experimental. Emits IR strokes with a WIDTH channel and
`STROKED_VARIABLE` appearance; tool family is always PEN until the
pen-type field is found.

## index.events.pb — the document's source of truth `[verified 2026-07-09]`

A stream of `<varint len><record>` protobuf records. The app derives the
document from THIS journal at import — `index.notes.pb` alone is just a
storage index. Records observed in a real Mac export, in order:

| record | top field | role |
|---|---|---|
| 0 | 30 | document-created: doc uuid, title+version, first-page ref, `"P"`, `"auto"`, timestamps (double ms + varint ms), device id, schema 24 |
| 1 | 6 | attachment-added: attachment uuid ×2, byte size, doc uuid |
| 2 | 2 | **paper definition**: attachment ref, scale 29.333…, page size floats (834.24 × 1078.825 for "standard"), paper name string (`"<uuid>_standard_1_1 - Yellow"`), margins struct |
| 3 | 54 | **page-created**: page ENTITY uuid, paper ref, lexicographic ORDER KEY (e.g. `"43elQ2"`), page colors |
| 4 | 10 | view state (`PagingViewServiceUpdater:…`) — optional |
| 5 | 105 | **page-link**: page number, doc uuid, page CONTENT uuid (= the `notes/<uuid>` member name), `"auto"` |
| 6–10 | 104/105/102 | further view/settings events — optional |

Import behavior `[verified by iteration]`: without records 2/54/105 the
container imports but shows **zero pages**. The page ENTITY uuid (54)
and page CONTENT uuid (105 / member name) are allocated ADJACENTLY:
`entity = content − 1` (last hex group decremented). With random entity
uuids the page materializes but stays blank; with the adjacency the ink
attaches `[inferred from one sample + confirmed import behavior]`. The
device id (varint, ~62 bits) is shared between the events journal and
every page-journal header.

## Writer (experimental, validated=False)

`core/src/inkterop/formats/goodnotes/writer.py` — the exact inverse of the
reader's consumption, gated behind `--experimental` until the GoodNotes
Mac app-import check passes (docs/validated-writes.md). What it emits:

- **Container**: ZIP with `schema.pb` (field 1 varint = 24), `index.notes.pb`
  (one delimited record per page: field 1 = page UUID, field 2 =
  `notes/<UUID>` path — both fields as observed in the Mac-export fixture),
  one `notes/<UUID>` per page, and a tiny white `thumbnail.jpg`. This is
  the **minimum our reader needs**; the app's other members
  (index.events.pb, index.search.pb, index.attachments.pb,
  document.info.pb, search/) are not written — whether the app tolerates
  their absence is `[unknown]`.
- **Page stream**: branches on the source schema version (captured by
  the reader in `doc.extra["goodnotes"]["schema_version"]`). Schema 24:
  flat metadata record + stroke records. Schema 25: the (header, payload)
  journal pairs described above; round-trips REPLAY the source stroke
  message byte-faithfully with only the journal linkage fields (1 uuid,
  15 echo) rewritten — the app's geometry blobs carry per-pen sections
  our minimal encoder can't rebuild, and a re-encoded brush stroke
  renders as a blob in-app. The first (header, page-item) pair is
  replayed verbatim from `page.extra["goodnotes"]["meta_record"/"meta_payload"]`.
- **Events journal**: document-created, attachment-added, paper
  definition, and per-page page-created + page-link records (see the
  events section) — the import-blocking set as of GoodNotes Mac 6
  (2026-07-09).
- **Geometry**: every stroke uses the pressure-pen tpl signature
  (`vA(v)A(u)A(u)A(v)A(v)A(u)A(u)A(u)A(u)A(v)`) with a 3-float anchor and
  flat (x, y, width) triplets; the constant-width/pencil/brush section
  layouts are not re-emitted. LZ4 framing uses raw `bv4-` blocks
  (≤ 64 KiB each) + `bv4$` — legal frames, zero compression (the ZIP
  deflates on top). Triplets are clamped to the reader's plausibility
  window and dots/two-point strokes are padded to 3 points; points at
  (~0, ~0) are nudged off the sub-path-break sentinel.
- **Pen types**: NativeTool ids from a GoodNotes source round-trip
  verbatim; otherwise highlighter → 4, pencil → 3, everything else → 0.
- **Fidelity**: `exact` emits per-point rendered widths (appearance.width
  or the WIDTH channel), `native` constant family-default widths
  (24 pt highlighter, 1.56 pt pens), `raw` raises (GoodNotes stores
  rendered widths, not raw dynamics).
- **Page dimensions**: the dims field is still `[unknown]`, so written
  pages implicitly assume A4; ink extents drive the reader's bounds.

## Open questions (corpus cases that resolve them)

1. Pen-type id → UI tool names — case 05 with labeled per-tool files.
2. Raw dynamics: pressure-pen section 9 columns and pencil c3/c4 tilt —
   cases 16, 17 (iPad+Pencil).
3. Page dimensions field (reader currently grows bounds to ink extents;
   the mixed-pens fixture page is wider than A4) — case 14.
4. Shape-tool geometry location (type-7 strokes have empty inline
   geometry) — case 09.
5. Eraser representation — case 08.
6. Images & text boxes — cases 10, 11.
7. PDF background linkage (attachments ↔ pages) — case 12.
8. index.events.pb / index.search.pb / document.info.pb contents.
9. Paper template (grid/lined background) encoding — nothing
   template-like found in the page records yet.

## Changelog

- 2026-07-09 (round 3, autonomous app loop): schema-25 page-file JOURNAL
  structure decoded (header/payload pairs, uuid+echo linkage) via import
  bisection; events-log page model decoded (paper/page-created/page-link
  records, entity = content−1 adjacency); writer branches per schema and
  replays source stroke messages verbatim on round-trips. RESULT:
  goodnotes-roundtrip imports + renders fully in GoodNotes Mac. Open:
  foreign (synthesized) geometry — the app accepts but does not RENDER
  our minimal tpl sections under schema 25, and rejects flat schema-24
  containers outright; next step is decoding the remaining pressure-pen
  sections (self-drawn Mac probes now feasible with app control).
- 2026-07-09 (night): experimental writer (validated=False): wire/LZ4/tpl
  encoders as exact decoder inverses; minimal container; pressure-pen
  triplet geometry for all pen types; fixture write→read round-trip green.
- 2026-07-09 (evening): first controlled Mac-app export (schema 25):
  pen-type field found and verified; full signature grammar (structs,
  segment arrays); constant-width and pencil layouts; 9-float brush
  segments; adaptive page bounds. Fixture committed.
- 2026-07-09: initial spec from public samples; typed-section layout of
  the tpl blob; independent LZ4/protobuf decoders; schema.pb identified as
  version marker.
