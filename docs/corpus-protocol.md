# Corpus protocol

The controlled-corpus procedure for closing the open questions in
`docs/formats/goodnotes.md` and `docs/formats/notability.md`. Read
`docs/reverse-engineering.md` first — this is the concrete recipe for that
doc's "known-shape corpus experiments" step, scoped to the Mac App Store
builds of GoodNotes and Notability, to be extended with iPad+Pencil cases
once that hardware is in the loop.

## Per-case procedure

For every numbered case below:

1. Create a **new document from the app's default template**.
2. Perform **exactly one action** (draw one stroke, change one setting,
   insert one object — whatever the case specifies). One action per case
   keeps the diff attributable.
3. Export **both**: the app's native format (`.goodnotes` / `.note`) *and*
   the app's own PDF export of the same document. The PDF export is what
   the rendering-validation gate (`docs/reverse-engineering.md`) diffs our
   parse against — always take it in the same pass so it's guaranteed to
   reflect the exact same document state.
4. Record the file in `corpus/manifest.toml` with fields: `file`,
   `source = "self"`, `case` (the number/slug below), `app`, `app_version`,
   `os_version`, `date`, `sha256`.

Naming convention: `gn-NNN-slug.goodnotes` (GoodNotes) /
`nb-NNN-slug.note` (Notability), `NNN` zero-padded to the case number.
Example: `gn-02-horizontal-line.goodnotes`.

### Diff pairs

For cases where the isolating signal is "what changed in the file," export
twice: once after the case's one action, then perform **one more action**
and export again (both native and PDF). Binary-diffing the two native
exports isolates exactly the bytes that second action touched — this is
the fastest way to locate an unknown field once its rough location is
already known from the typed-section/protobuf structure (e.g. isolating
which byte range flips when going from "no highlighter" to "highlighter"
on an otherwise identical stroke).

### Container snapshots

Before and after each save, snapshot the app's container directory
(`~/Library/Containers/<bundle-id>/`) to find where the app keeps its
working store before a formal export — GoodNotes/Notability may stage
data differently from what an "export" produces, and the live container
can reveal intermediate state an export normalizes away. `dump-container.py`
(planned, `tools/re/`) will automate the before/after diff; until it
exists, do this manually with `tar` or `rsync -av --checksum` snapshots to
a scratch directory.

## The numbered case matrix

| # | Case | Isolates |
|---|---|---|
| 00 | Empty doc | Baseline container/index shape with zero strokes |
| 01 | Single dot (tap, no drag) | Minimal single-point stroke encoding |
| 02 | ~100pt horizontal line at a known grid position | Axis mapping, coordinate units, page-space origin |
| 03 | Corner-to-corner diagonal | Origin corner, y-axis direction |
| 04 | Two strokes | Multi-stroke record framing/ordering |
| 05 | One stroke per pen type (every tool the app offers) | Pen-type field |
| 06 | Each preset color + one custom color | Color field encoding, preset vs. custom representation |
| 07 | Highlighter stroke | Highlighter flag/opacity vs. regular pen |
| 08 | Partial erase + whole-stroke erase | Eraser representation (point removal vs. tombstone vs. new geometry) |
| 09 | Shape-tool object (e.g. a drawn rectangle/circle if the app auto-shapes it) | Shape-object encoding vs. freehand stroke |
| 10 | Text box "Hello" | Typed-text object encoding |
| 11 | Inserted image | Image/attachment encoding and page linkage |
| 12 | One-page PDF imported as background + one annotation stroke | PDF-background linkage (attachment ↔ page ↔ ink) |
| 13 | Three-page doc, stroke on page 2 only | Page ordering/indexing with sparse ink |
| 14 | Non-default paper size + landscape | Page-dimension field |
| 15 | Short audio recording (**Notability only**) | Audio attachment encoding |
| 16 | Pressure ramp stroke (**iPad+Pencil only**) | Per-point pressure/width channel mapping |
| 17 | Tilt/azimuth stroke (**iPad only**) | Tilt/azimuth channel presence and mapping |
| 18 | Same doc exported from Mac and iPad | Mac-corpus parity check — confirms cases 00–17's findings hold for the iPad-authored files too, not just Mac-authored ones |

Cases 16–18 require iPad+Pencil hardware and are deferred until that's
available; 00–15 (00–14 for GoodNotes, 00–15 for Notability, whose audio
support GoodNotes lacks) run on the Mac App Store builds first.

## Per-case "resolves" — what each case is *for*

Point every case result back at the specific open question it's meant to
close. From `docs/formats/goodnotes.md`'s open-questions list and
`docs/formats/notability.md`'s open-questions list:

- **GoodNotes field 14/15/7** (pen type / highlighter flag, currently
  unlocated) → cases **05** (one stroke per pen type — diff the candidate
  fields across pen types) and **07** (highlighter — diff against a
  same-color, same-width regular pen stroke from case 05).
- **GoodNotes geometry-blob section 9** (5 floats/point — x, y, w, +2
  unknown columns, suspected pressure/tilt) and **Notability
  `curvesfractionalwidths` → stroke mapping** (count mismatch: fractions
  present don't sum to total points) → case **16** (pressure ramp,
  iPad+Pencil): for GoodNotes, watch which of section 9's extra two
  columns moves monotonically with applied pressure; for Notability,
  watch whether `numfractionalwidths` starts matching `numpoints` for a
  pressure-drawn stroke, which would confirm fractions are per-stroke and
  only present for pressure-capable input.
- **GoodNotes page-dimension field** (reader currently assumes A4
  unconditionally) → case **14** (non-default size + landscape): compare
  `index.notes.pb` / page metadata across a default-size doc and this one
  to isolate the field.
- **GoodNotes eraser representation** → case **08**.
- **GoodNotes images & text boxes** (unmapped) → cases **10**, **11**.
- **GoodNotes PDF-background ↔ page linkage** → case **12**.
- **GoodNotes `index.events.pb` / `index.search.pb` contents** → any case
  with a diff pair (repeated action) showing how those indexes change
  incrementally.
- **Notability `eventTokens`, `InkedSpatialHash` internals** → likely
  derived/cache data; lower priority, revisit if a case's diff pair
  implicates them directly.
- **Notability page/paper metadata location + PDF-background alignment**
  → cases **12**, **14**.
- **Notability current-app export shape** (legacy zip+plist vs. a newer
  "Notability Cloud" format) → case **00**, day one: does a fresh
  Mac-app export still produce the legacy `Session.plist` shape the
  reader currently expects, or something else? This gates whether the
  rest of the matrix is even meaningful for the current app version.

## Corpus → fixtures promotion

Per `docs/reverse-engineering.md`'s sample-ethics section: cases whose
result is small and contains no personal data get promoted from
`corpus/scratch/` (gitignored) into `core/tests/fixtures/<format>/`
(committed, CC0) once a reader exists to exercise them. Third-party
samples (e.g. `corpus/third-party/goodparse/samples/`) never get promoted
— only self-generated corpus files following this protocol do.
