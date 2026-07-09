# GoodNotes (.goodnotes) format

Status: **ink strokes + color + pen-type field decoded** across both
observed schema versions. Verified against public GoodNotes 6 samples
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
index.events.pb          [unknown] (edit history?)
index.search.pb          [unknown] (handwriting search index?)
index.attachments.pb     attachment index (protobuf)
schema.pb                2 bytes: field 1 varint = schema version
                         (observed 24, 25) — NOT an embedded schema [verified]
document.info.pb         schema 25+: empty (0B) in observed exports [unknown]
search/<UUID>            schema 25+: tiny per-page blobs [unknown]
```

## Page files `notes/<UUID>` `[verified]`

A stream of length-delimited protobuf records: `<varint len><message>`,
repeated. Two record shapes observed:

- **Metadata records** — fields `1` (36-char UUID string), `2` (8 bytes),
  `3` (varint), `8`/`9` (varints; look like timestamps/sequence numbers
  `[inferred]`), `16` (varint 24 = schema version `[inferred]`).
- **Stroke records** — a single field `7` containing the stroke message.

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

- 2026-07-09 (evening): first controlled Mac-app export (schema 25):
  pen-type field found and verified; full signature grammar (structs,
  segment arrays); constant-width and pencil layouts; 9-float brush
  segments; adaptive page bounds. Fixture committed.
- 2026-07-09: initial spec from public samples; typed-section layout of
  the tpl blob; independent LZ4/protobuf decoders; schema.pb identified as
  version marker.
