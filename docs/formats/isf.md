# Microsoft Ink Serialized Format (.isf)

Status: **read support (honest subset)**. Stdlib-only independent
implementation in `core/src/inkterop/formats/isf.py`. Where it comes
from — both sources fully permitted:

- the [MS-ISF specification](https://learn.microsoft.com/en-us/uwp/specifications/ink-serialized-format)
  (Microsoft Open Specification Promise; the PDF mirror at loc.gov has
  the full Huffman tables and tag list);
- Microsoft's own ISF codec inside WPF
  ([dotnet/wpf](https://github.com/dotnet/wpf), MIT;
  `PresentationCore/MS/internal/Ink/InkSerializedFormat/`), which
  resolves everything the spec text leaves ambiguous.

Verified against 11 real ISF files written by Microsoft's own encoder
(dotnet/wpf-test, MIT; corpus-gated in
`corpus/third-party/wpf-test-isf/`) plus 3 self-made fixtures
(`core/tests/fixtures/isf/`). ISF is also the ink layer inside OneNote
`.one` files; the primitives here (multibyte ints, sign-flip signed
values, delta-delta, Huffman/bit-pack decoding) are importable for that
future reader.

No pip-installable permissive oracle exists (isf-qt is GPL C++, the
reference codec is .NET), so there is no oracle test; the corpus files
serve that role structurally.

## Container `[verified]`

```
mbuint version        must be 0 ("ISF 1.0")
mbuint stream_size    bytes that follow
<tagged blocks ...>
```

- **mbuint**: 7-bit little-endian groups, high bit = continuation
  (protobuf-varint compatible). **Signed** values are sign-flipped:
  `(abs(v) << 1) | sign` — *not* zigzag.
- Tags: 0–30 structural (31 = extended transform table, WPF addition),
  50–87 = predefined property GUIDs (`50 + GUID index`), ≥ 100 = custom
  GUIDs (`tag - 100` indexes the GUID table). Tags 32–49 reserved.
- Most blocks are `tag, mbuint size, payload`. Exceptions `[verified]`:
  ink-space rect (tag 0; four signed mbints l,t,r,b, no size field) and
  the bare single-transform tags 16–21 (fixed float payloads).
- Some containers hold **multiple concatenated ISF streams** (observed
  in Microsoft's own test data); the reader parses the first stream and
  `detect()` tolerates trailing data.

### Tag map `[verified: spec appendix + WPF KnownTagCache]`

| # | tag | payload |
|---|-----|---------|
| 0 | INK_SPACE_RECT | 4 signed mbints (no size) |
| 1 | GUID_TABLE | size; n×16 raw GUID bytes |
| 2 | DRAW_ATTRS_TABLE | size; repeated (size + DA block) |
| 3 | DRAW_ATTRS_BLOCK | size; property list (single-block case) |
| 4 | STROKE_DESC_TABLE | size; repeated (size + descriptor block) |
| 5 | STROKE_DESC_BLOCK | size; descriptor (single-block case) |
| 6/7/8 | BUTTONS / NO_X / NO_Y | inside descriptor blocks |
| 9/13/23/26 | DIDX/SIDX/TIDX/MIDX | mbuint index; applies to following strokes |
| 10 | STROKE | size; mbuint cPoints, packet arrays, buttons, stroke props |
| 11/12 | STROKE_PROPERTY_LIST / POINT_PROPERTY | stroke-scoped, skipped |
| 14 | COMPRESSION_HEADER | skipped (custom Huffman codecs unsupported) |
| 15 | TRANSFORM_TABLE | size; repeated (tag + float payload) |
| 16–21 | TRANSFORM_* | bare single transform (floats; ROTATE = mbuint centidegrees) |
| 24/25 | METRIC_TABLE / METRIC_BLOCK | size; metric entries |
| 27 | MANTISSA | pen width/height fraction refinement (skipped) |
| 28 | PERSISTENT_FORMAT | mbuint (0 = ISF, 1 = fortified GIF) |
| 29 | HIMETRIC_SIZE | 2 signed mbints |
| 30 | STROKE_IDS | skipped |
| 31 | EXTENDED_TRANSFORM_TABLE | doubles re-statement of 15 (WPF v2) |

Packet property tags used for IR: X=50, Y=51, TIMER_TICK=54,
NORMAL_PRESSURE=56, X/Y_TILT=59/60, AZIMUTH=61, ALTITUDE=62. Drawing
attribute tags: PEN_STYLE=67, COLORREF=68, PEN_WIDTH=69, PEN_HEIGHT=70,
PEN_TIP=71, DRAWING_FLAGS=72, TRANSPARENCY=80, ROP=87.

## Packet (per-point) compression `[verified: WPF AlgoModule]`

Each property array in a stroke = 1 algorithm byte + data; no stored
byte length (the decoder knows cPoints). Bits pack **MSB-first**.

- `0x80 | i` — **indexed Huffman**, table `i` (must be < 8; ≥ 8 would
  reference a custom codec from TAG_COMPRESSION_HEADER — unsupported,
  raises). Values are **always delta-delta transformed**
  (`dd_i = v_i + v_{i-2} − 2·v_{i-1}`, zero-initialized state). Code:
  N one-bits + a zero (N=0 ⇒ value 0), then `bits[N]` payload bits =
  `(|v| − mins[N]) << 1 | sign`; a prefix of table-size ones is the
  64-bit escape (extra value = high bits, then low 32). The 8 tables
  and the `mins[]` construction are in the spec appendix
  (`DEF_BAA_DATA`).
- `0x00 | flags` — **bit-packing**: low 5 bits = bits/value (0 ⇒ 32),
  bit `0x20` = delta-delta, in which case the first two transformed
  values are multibyte sign-encoded and the remaining n−2 bit-packed.
  Values are two's-complement in the bit width. Algo byte `0x00` ==
  raw big-endian int32.
- `0xC0`/`0xF0` (DEFAULT/BEST_COMPRESSION) never appear in files —
  they are encoder-side requests `[verified: WPF]`.

What real files use (histogram over the 11 corpus files): Huffman
tables 2–7 for the overwhelming majority of arrays, plain bit-packing
(2–17 bits) as the fallback, no delta-delta bit-packing and no raw
0x00 observed. (WPF's encoder has an inverted `input.Length < 3` test
that suppresses delta-delta bit-packing entirely; the decoder handles
it per spec, including the n−2 count — WPF's own byte accounting for
that path is buggy, we implement the spec.)

Property (byte-array) payloads have their own algo byte: bit-packing
of bytes/words/ints via the spec's cBits-cPads table (implemented,
`decompress_property`) or LZ (`0x80`, **unsupported**, raises — never
emitted by WPF either).

## IR mapping

- Coordinates are HIMETRIC (1 unit = 0.01 mm) after applying the
  stroke's transform (default identity): `point_scale = 72/2540`,
  y-down. Transform layout `(m11,m12,m21,m22,dx,dy)`,
  `x' = m11·x + m21·y + dx` `[verified: WPF]`.
- NORMAL_PRESSURE → `Channel.PRESSURE`, normalized by its metric entry
  (default 0..1023 `[verified: WPF defaults]`), clamped 0–1.
- AZIMUTH (default 0.1° units) → `TILT_AZIMUTH` radians via
  `radians(90 − deg)` `[inferred: ISF azimuth is clockwise-from-north;
  IR wants CCW-from-+x]`. ALTITUDE (default 0.1° units, −90..90°) →
  `TILT_ALTITUDE` radians directly (π/2 = perpendicular, matching the
  IR contract).
- TIMER_TICK → `TIMESTAMP` seconds since stroke start, assuming
  millisecond ticks `[inferred — no spec statement; metric marked NA]`.
- X_TILT/Y_TILT and other packet properties (PACKET_STATUS, serial,
  buttons…) are decoded/skipped for framing but not mapped (IR has no
  channel for them).
- DrawingAttributes → `StrokeAppearance` (STROKED_CONSTANT):
  COLORREF `0x00BBGGRR` → color; transparency byte t → opacity
  `(255−t)/255`; PEN_WIDTH/HEIGHT are HIMETRIC (defaults: 53 when no
  DA at all `[inferred: v2 default 0.53 mm]`, 25 when a DA exists but
  stores width 0 `[verified: WPF]`); PEN_TIP 1 (rectangle) →
  `LineCap.SQUARE`; raster op 9 (MaskPen) → highlighter: family
  HIGHLIGHTER, `underlay=True`, `BlendMode.DARKEN` `[verified: WPF
  IsHighlighter]`. Native tool params carry pen_tip/pen_style/
  raster_op/drawing_flags/raw width/height/transparency.
- Single page; bounds = content bbox unioned with the ink-space rect
  when present. Ink-space rect / himetric size / custom GUIDs / global
  properties (raw hex) land in `Document.metadata`.

## Subset limitations (parsed for framing, not interpreted)

- Stroke extended properties and point properties (byte count comes
  from the stroke block size; total skipped bytes reported in
  `metadata.skipped_stroke_property_bytes`).
- Custom GUID property *semantics*: payloads are kept as raw hex in
  metadata, no VARIANT decoding (recognition lattices, Word
  alternates, GUIDE_STRUCTURE...).
- Button states (bit-packed after the packet arrays) are skipped.
- Fortified-GIF persistence (`gif.isf`-style files are GIFs with an
  ISF `fortification` chunk) is not handled — those are image files.
- TAG_MANTISSA width refinements (< 0.001 himetric) are skipped.
- Only the first ISF stream of a multi-stream container is read.
- LZ property payloads and custom Huffman codecs raise `IsfError`.

Deliberate divergences from WPF quirks: TRANSFORM_ROTATE builds a
proper rotation matrix (WPF sets `M10 = −cos` instead of `−sin`);
metric entries assign min/max as read (WPF discards the last field
read before a boundary); delta-delta bit-packed byte accounting uses
the spec's n−2 packed values.

## Open questions

- `[unknown]` Exact azimuth zero direction/handedness — no
  tilt-carrying sample in the corpus; the mapping is self-consistent
  with the fixture encoder only.
- `[unknown]` TIMER_TICK unit (ms assumed) and epoch.
- `[unknown]` TAG_TRANSFORM_QUAD (22) payload — never observed; WPF
  doesn't parse it either.
- Writing ISF (the fixture generator is a minimal encoder; a real
  writer needs the validated-writes policy treatment).

## Changelog

- 2026-07-09: initial reader + fixtures + corpus validation (11
  dotnet/wpf-test files: Huffman tables 2–7, bit-packing, metric
  blocks, transform tables, DA tables incl. highlighter/pressure).
