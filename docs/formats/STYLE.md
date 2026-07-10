# Format documentation style guide

Every format gets a directory under `docs/formats/<name>/` with two pages:

- **`protocol.md`** — the data format: bytes, fields, framing. Written so
  someone with a hex editor and no access to our code can implement an
  independent parser.
- **`rendering.md`** — how strokes become pixels in the source app: the
  measured rendering rule, tool table, page geometry, blend quirks.
  Written so someone can reproduce the app's output to the pixel.

Single-page docs (`docs/formats/<name>.md`) are the legacy layout; they
migrate when a format's rendering rule gets measured. Keep the old URL as
a stub linking to the new pages.

## Voice

Write like the protobuf encoding guide or good Android platform docs:
declarative, concrete, zero filler. Rules of thumb:

- Every claim carries a confidence marker (`[verified]` / `[inferred]` /
  `[unknown]`) and verified claims say *how* they were verified ("case 07,
  predicted then confirmed", "zero-residual parse across 11 samples").
- Show, then tell: lead each section with a real byte dump or record, then
  explain the fields. Never describe a layout you could just show.
- Numbers are exact: "u32 little-endian at offset 0x10", not "a length
  field near the start".
- No hedging padding ("it seems", "likely", "appears to") — that's what
  the confidence markers are for.
- History goes in the changelog, not inline ("as of v6…" belongs in a
  versioned section, not sprinkled through prose).

## protocol.md skeleton

````markdown
# <Name> data protocol

Status line: what's decoded, verified against which app version, date.

## Container
One annotated tree of the file/zip/dir layout.

## <Record type> layout
An annotated hexdump of a REAL record from a fixture (offsets, bytes,
meaning side by side):

```
00000000  74 70 6c 00 a1 02 00 00  "tpl\0"  u32 total_len = 0x2a1
00000008  58 59 57 50 ...          type signature "XYWP..."
```

Then the field table (protobuf-doc style):

| # | offset/tag | type | name | meaning | confidence |

## Worked example
Decode ONE stroke/record by hand, start to finish, from fixture bytes to
final values — the "can a human follow this" test.

## Quantities & units
Coordinate space, units, y-direction, scale factor derivation.

## Changelog
Dated findings, including app-version drift checks.
````

## rendering.md skeleton

````markdown
# <Name> rendering

Status line: which rules are measured vs inferred, against which app
version + export path (vector PDF export ≠ canvas raster — say which).

## Rendering rule
The measured law, as executable math with fitted constants:

```
thickness(p) = strokeWidth × 8.5 × sin(π/2 × (0.5 + 0.6·(p − 0.5)))
```

State: the corpus case that measured it, the fit residual, the code that
implements it (`formats/<x>.py:<symbol>`), and the inverse used by the
writer.

## Tool table
| tool id | UI name | width source | opacity/texture | cap/join | blend |

## Page geometry
Canvas size, origin, orientation handling, export scale.

## Known quirks
Blend approximations, single-point strokes, overlap behavior — each with
the observation that established it.

## Changelog
````

## The bar

A page is done when:

1. **protocol.md**: a reader can hand-decode a fixture record with only
   the page open (the worked example proves it).
2. **rendering.md**: the constants in the page match the constants in
   code (cite file:symbol), and every rule links the corpus case that
   measured it.
3. Every `[unknown]` byte range in observed samples is at least listed —
   silent gaps read as "fully decoded" when they aren't.
