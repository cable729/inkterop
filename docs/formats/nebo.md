# Nebo / MyScript Notes (.nebo) format

Status: **container mapped; BINK v5 ink codec decoded** (geometry
`[verified]`, styling/tag table `[inferred]`). Verified against a
controlled Nebo iPad 7.4.3 (iink SDK 4.4 / core 8.13.0, iOS 26.5)
export, 2026-07-09, by scripted overlay-diff against the app's own SVG
export of the same document. Prior research called .nebo "un-reversed".
Reader: `core/src/inkterop/formats/nebo/reader.py`; fixture:
`core/tests/fixtures/nebo/nebo-ipad-pen-highlighter.nebo` (self-drawn,
CC0).

## Container `[verified]`

```
rel.json                  {"pages": {"<id>": {"version": 5}}} page list
index.bdom                magic "BDOM" v2: document object model, binary
meta.json                 app/version metadata, pageTitle,
                          pageExtent [0,0,210,297] (millimeters, A4)
pages/<id>/ink.bink       magic "BINK" v5: THE INK (spec below)
pages/<id>/page.bdom      per-page BDOM (readable ASCII fragments)
pages/<id>/meta.json      page metadata (pageExtent again)
pages/<id>/style.css      plain CSS (".smartpen {...}")
```

The container shape is identical between the Mac MyScript Notes export
inventoried earlier and the iPad Nebo sample decoded here (answers open
question 3 of the previous revision — same zip layout, same BINK v5).

## BINK v5 ink codec

All little-endian, byte-packed (no alignment). Strings are
`u32 length + bytes`. Full-file parse of the reference sample leaves
40 residual bytes (see "Trailing record", below); everything else is
accounted for.

### Header `[verified]`

```
"BINK\0"  u32 version(=5)  u8 0  u32 1
u32 nchannels (=4)
per channel:
  str name        "X", "Y", "F", "T"
  u8[4] type tag  20 04 01 00 for X/Y/F, 20 02 01 00 for T  [unknown]
  u32 has_unit    1 -> str unit ("mm" for X/Y, "ms" for T; F has none)
u32 layout_len (=0x44)
  u32 nentries (=4), per entry: u32 u32 u32 (X:(0,0,8) Y:(4,0,8)
  F:(0,8,4) T:(0,12,4)) + the channel's 4-byte type tag — looks like
  offsets/sizes of a 16-byte full-precision point struct  [unknown]
u32 precision_x (=1000)  u32 precision_y (=1000)   [inferred: precision]
u32 unk (=3)  u8 0                                  [unknown]
u32 nstrokes (=2)
```

### Stroke records `[verified]`

```
u32  flags        0x80000000 = live stroke; 0xffffffff = TOMBSTONE:
                  an erased stroke leaves this single -1 word as the
                  whole record and still counts toward nstrokes.
                  Tag-table stroke indices count tombstones.  [verified]
                  (Apple-Pencil calibration page, Nebo iPad 7.4.3:
                  36 records = 32 live + 4 tombstones)
u64  t0           MICROSECONDS since Unix epoch. Cross-checked two
                  ways: value = sample's creation date, and the tag
                  table's "DWContentFieldName": "1/Text<t0>" reuses it.
f32  x0, y0       first point, millimeters, y-down, page top-left
                  origin (same frame as the app's SVG export)
u32  0x0c4910b9   constant across strokes                [unknown]
u16  0
u32  n            point count
i16  dx[n]        X first differences (dx[0] relative to x0, = 0)
i16  dy[n]        Y first differences
u8   f[n]         force; 255 everywhere for capacitive-pen input.
                  Apple Pencil stores real values (0..~250 observed on
                  the calibration page; every stroke varies) [verified]
```

Position: `x_mm = x0 + cumsum(dx)/500` — **1 delta unit = 2 µm**.
The /500 is empirical (`[verified]` by overlay-diff, see below); how it
relates to the header's `precision = 1000` is an open question (could
be `2/precision`; a sample with a different precision would isolate
it).

The declared T (ms) channel has **no per-point storage** — only the
stroke's `t0` survives; `5n` bytes per stroke = 2n (dx) + 2n (dy) + n
(f), zero residual on both strokes.

### Tag table (annotations over strokes) `[inferred]`

Follows the last stroke:

```
u32 0   u32 record_count (=28)   u8 0
records; record 0 has NO head, records 1..count-1 have:
  u32 kind      12, 0, 11, 100 ('d'), 103 ('g'), 105 ('i')  [unknown]
  u32 id        sparse, increasing (0,1,2,4,5,7,...)
  u32 0
each record:
  str  name
  u32 ngroups       span-group count — 1 in most records; Apple-Pencil
                    pages emit multi-group records (e.g. 3)  [verified]
  ngroups x:
    u16 3   u16 span_start   sample offset within the FIRST stroke
    u32 first_stroke  first stroke record of the tagged run  [verified]
    u8 u8           usually 05 ff; 01 00 on one partial-span CHAR
    u16 span_end    last sample index within the LAST stroke (n-1 for
                    full-stroke tags; CHAR spans split at recognition
                    boundaries)
    u32 last_stroke first == last for single-stroke tags; record
                    indices count tombstones                 [verified]
  u32 str_len + utf-8 payload (may be empty; shared by all groups)
```

A tag covers the inclusive stroke-record range
`[first_stroke, last_stroke]` (verified on the calibration page:
`HIGHLIGHT_STROKES`/`brush-0500` carries `first=28, last=35`, exactly
the 8 highlighter strokes, with `span_end = 34` = the last stroke's
final sample; every pen run's `.STYLE` ranges tile the pen strokes).

New tag names seen on Apple-Pencil pages: `active-pen-input` (input
device marker), `component-brush`, `brush-oriented`, and highlighter
styling `"-myscript-pen-pressure-sensitivity: 0;color:#FFDD3366"`
(alpha byte in the color). The former open question — a
`HIGHLIGHT_STROKES`/brush record seeming to anchor only ONE stroke of
a drawn row of 8 — is resolved: the record's two stroke fields are a
first/last range and the tag covers the whole run; the reader now
styles every stroke in it.

Observed record names, per stroke:

- input/tool: `capacitive-pen-input`, `pen-025`, `component-brush`,
  `brush-0500`, `brush-oriented` — brush names encode width in
   1/100 mm (`pen-025` = 0.25 mm pen, `brush-0500` = 5 mm highlighter;
  the 5 mm matches the SVG export's outline band width) `[inferred]`
- styling: `.STYLE` with a CSS-ish string payload, e.g.
  `"color:#000000ff;-myscript-pen-pressure-sensitivity: 0.57;"` (pen)
  and `"-myscript-pen-pressure-sensitivity: 0;color:#FFDD3366"`
  (highlighter). Color is `#RRGGBBAA` (AA=66 -> the translucent
  yellow). Text-block `.STYLE`s carry `line-height`/`font-size`.
- grouping: `TEXT_STROKES`, `HIGHLIGHT_STROKES`, `LAYOUT_STROKES`,
  `INPUT`, `SET_AS_DRAWING`, `writing-position-middle-right`
- recognition output: `CHAR`/`WORD`/`TEXT`/`TEXT_LINE`/`TEXT_BLOCK`
  spans (the sample's pen scribble was recognized as "W") and
  `DIAGRAM` records with JSON payloads (`"DWShape": "text"|"freedraw"`,
  `"DWTagId"`, `"DWContentFieldName": "1/Text<t0_us>"`).

**Trailing record `[unknown]`**: after `record_count` records, the
sample has 40 more bytes shaped like one more (name-less, truncated)
record: `kind=0x0b id=0x1f 0 0x20 0 <20-byte payload>`. Not consumed
by the reader.

### Rendering notes

- **Measured width law** (calibration page, Nebo iPad 7.4.3, Apple
  Pencil, oracle = the app's own SVG export; fitted 2026-07-10):

  ```
  rendered_width = base_width × (1 + sensitivity × 2.43 × (force − 0.29))
  ```

  where `base_width` comes from the brush name (pen-025 = 0.25 mm),
  `force` = `f[n]/255`, and `sensitivity` is the .STYLE
  `-myscript-pen-pressure-sensitivity` value. Measured ribbon widths
  span 0.33–2.44 × base over the force range and cross 1.0 × base at
  force ≈ 0.29 `[verified at sensitivity 0.8]`. The highlighter
  (sensitivity 0) renders constant 1.06 × base — consistent with the
  same law at s=0, so the sensitivity parametrization is `[inferred]`
  (only s=0.8 and s=0 sampled). Constants + inverse live in
  `ir/renderrule.py`; per-force bin table in
  `docs/calibration-results.md`.
- The reader bakes this law into a per-point `WIDTH` channel whenever
  the force channel actually varies (real pen data). Capacitive input
  is constant f=255 — how the app renders those (speed-based?) is
  `[unknown]`, so they keep the constant-width appearance from the
  brush name.
- Style tags cover their whole stroke-record range (first/last fields,
  see the tag table above), so every pen stroke normally carries its
  `.STYLE` sensitivity; the reader still assumes the app-default 0.8
  for pen strokes left unstyled (e.g. an unparseable tag table)
  `[inferred]`.
- Highlighter: `#FFDD3366`, 5 mm, drawn as a translucent band.
- PDF export lane: registers now that convert defaults to native page
  sizing (A4 out for A4 in) and tags style whole runs — 68% registered
  ink-match on the calibration page; the remainder is cap shape and
  width-law detail. The SVG lane stays the finer geometry oracle.

## Validation

Scripted overlay-diff of decoded centerlines vs the app's own SVG
export (outline polygons, mm, same A4 frame — no transform needed):

- pen stroke (280 pts): mean distance to outline 0.094 mm,
  max 0.19 mm ≈ the pen halfwidth;
- highlighter (178 pts): mean 1.16 mm, max 2.62 mm ≈ the 2.5 mm
  halfwidth (centerline mid-band);
- bbox corners agree within 0.2 mm + halfwidth on all four edges of
  both strokes.

## SVG export lane `[verified]`

MyScript Notes/Nebo export SVG with `viewBox="0 0 210 297"` (A4 in
**millimeters**) where each stroke is a **filled outline polygon** —
variable width baked into geometry, no centerline/pressure. Useful as
ground truth for BINK decoding (above); lossy for ink interchange.
JIIX (JSON ink with semantics) exists only in the MyScript SDK, not
the app.

## Open questions

1. Stroke-header constant `0x0c4910b9`, `flags=0x80000000`, header
   `unk=3`, the layout-table semantics, and the 40-byte trailer —
   diff-pair samples (one stroke added at a time; a non-A4 page; an
   active-pen device with real pressure) would isolate them.
2. The /500 delta scale vs the header's `precision=1000` (need a
   sample where precision differs).
3. BDOM: layout tree; needed for text blocks and non-ink objects
   (recognized text currently comes back only as tag-table spans).
4. ~~Do pressure-capable pens store varying `f[n]`?~~ Answered
   2026-07-10: yes — the Apple Pencil calibration sample carries real
   force (see Rendering notes). Still open: does any sample store
   per-point T? (The channel is declared but was absent again.)

## Changelog

- 2026-07-09: container inventory from a controlled Mac export; SVG
  export characterized (outline polygons, mm units).
- 2026-07-09 (later): BINK v5 decoded from a controlled iPad Nebo
  7.4.3 sample — header/channel table, stroke framing, delta point
  encoding (`[verified]` via overlay-diff vs the app's SVG export),
  tag table with styles/brushes/recognition spans (`[inferred]`).
  Reader + fixture + tests landed.
