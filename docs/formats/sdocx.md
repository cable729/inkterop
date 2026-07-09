# Samsung Notes (.sdocx) format

Status: **read support for ink** (strokes with pressure, tilt,
timestamps, pen identity, colours; text/image/shape objects skipped).
Format facts learned by reading [squ1dd13/sdocx2pdf](https://github.com/squ1dd13/sdocx2pdf)
(MIT — "parses the SDOCX format almost completely"; attributed in the
reader docstring) and verified empirically against the three public
twangodev/sdocx samples (corpus-gated; the repo is GPL but its samples
are study data only — never read its code, never commit the samples).

**No first-party corpus** — no Samsung device/app is available to the project, so nothing
here has been checked against Samsung Notes itself. `[verified]` below
means "consistent with the MIT reference implementation AND the three
corpus samples"; upgrades to app-verified are **hardware-deferred**
(corpus protocol cases: export + app PDF export from a real device).

Reader: `core/src/inkterop/formats/sdocx.py`. Self-made fixture:
`core/tests/fixtures/sdocx/` (generator + committed
`synthetic-two-page.sdocx`, CC0).

## Container `[verified]`

`.sdocx` = zip:

| Member | Contents |
|---|---|
| `pageIdInfo.dat` | sha256 of `note.note` + ordered page list (u16 count; per page: short-u16 string uuid + 32-byte sha256 of the page member's trailing hash) |
| `<uuid>.page` | one binary member per page — S-Pen SDK object tree |
| `note.note` | document header: dims, title/body rich text, **string registry** (pen names are interned here), pen presets, voice-recording index; ends with sha256 of itself |
| `media/mediaInfo.dat` | file registry (bind id → name+hash) for `media/*`; ends `"EOF"` (old) / starts with format version and ends `"EOFX"` (new) |
| `media/*` | page raster caches (`.spi`), embedded images, imported PDFs |
| `end_tag.bin` | doc metadata (app version, note w/h, paged vs pageless, orientation, encryption info); ends `"Document for S-Pen SDK"` |

The Windows app stores unexported notes as directories with the same
layout `[inferred from the reference]`.

Everything is little-endian. Recurring primitives `[verified]`:

- **bitfield**: u8 byte count (0–4) + that many bytes.
- **short string**: u16 char count + UTF-16-LE chars (u8 variant with a
  u16 *byte* count for object uuids).
- **timestamp**: i64 microseconds since epoch.
- **frame**: u32 size *inclusive of the size field* + payload. Frames
  carry a "flex offset" — where the flag-gated optional fields start,
  counted from the size field. Object frames also start with a u16 data
  type (0 = ObjectBase, 1 = stroke, 2 = text, ...).
- **hash chain**: sha256 everywhere — objects hash
  `sha256(uuid + str(mtime_micros))`, pages/layers/note.note hash their
  bytes. Our reader skips verification.

## Page member `[verified]`

Header: u32 page-end offset, u32 flex offset, property bitfield (bit 0
= text-only), field bitfield, then u32s orientation / width / height /
offset-x / offset-y, uuid, mtime, format + min-format version. The
flag-gated fields (drawn rect, tags, template, background colour/image,
embedded-PDF placements, canvas cache, recognition data...) sit between
flex offset and page-end offset — our reader seeks straight to page-end.
Then: u16 layer count, u16 current layer, layers, 32-byte page hash,
literal `"Page for SAMSUNG S-Pen SDK"`.

Layer = framed header (flex offset here is **absolute in the page
stream**, unlike object frames; optional alpha / background colour /
name / uuid / mtime / thumbnail id / shadow bytes) + u32 object count +
objects + 32-byte hash. Object = u8 type, u16 child count, u32 size,
`size` bytes = object frames + trailing 32-byte hash. Non-stroke types
(2 text, 3 image, 7 shape, 8 line, 10 audio, 13 web, 14 painting, ...)
are skipped by size.

## Stroke object (type 1) `[verified]`

ObjectBase frame (data type 0: flags, format version, uuid, mtime,
f64 bounding rect, resize mode + optional angle/bundles/...) — skipped
wholesale by our reader — then the stroke frame (data type 1):

Property bits: 0 curve/**compressed events**, 1 replay-only, 2 **tilt
data present**, 3 eraser-enabled, 4 fixed width, 5 millisecond mode,
6 top-layer pen, 7 alpha locked, 8 !binary-added, 10 !generated,
11 fixed opacity.

Then u16 event count, events, u16 tool type (0 unknown, 1 finger,
2 S-Pen, 3 mouse, 4 eraser), then at the flex offset the flag-gated
fields in bit order:

| bit | field |
|---|---|
| 1 | advanced pen settings — u32 **string registry id** |
| 2 | colour — 4 bytes **BGRA** |
| 3 | pen size — f32, page units |
| 4 | unknown u32 |
| 7 | pen name — u32 string registry id |
| 8 | fixed width f32 · 9 size level u32 · 10 particle density u32 · 11 rendering level u32 · 12 original width u32 · 13 initial tolerance f32 · 14 dash type u16 · 15 dash offset f32 · 16 stroke type u16 · 17 pen repeat distance f32 |

Bits 0/5/6 unknown → the reader hard-errors if set (they would
desynchronize every later field).

### Events

Uncompressed: x,y f64 pairs × n, pressures f32 × n, timestamps u32 × n,
then (if tilt) tilts f32 × n + orientations f32 × n.

Compressed ("curve" bit): full first event (x,y f64; pressure f32;
timestamp u32; tilt/orientation f32), then per remaining event u16
deltas, grouped per channel: point deltas (x,y interleaved, applied
x-first `[verified against the reference's behaviour — its variable
names say the opposite of what its code does]`) in sign+10.5 fixed
point (÷32), pressure/tilt/orientation deltas in sign+3.12 fixed point
(÷4096), timestamp deltas plain u16.

Semantics: pressure 0–1 `[verified]`; tilt radians, 0 = pen
perpendicular to page, π/2 = flat; orientation radians, 0 = tip toward
page top, +π/2 = tip toward right (Android axis convention)
`[verified]`. Event timestamps: u32 counter, assumed milliseconds
`[inferred]` (the "millisecond mode" property bit suggests another mode
exists — meaning `[unknown]`).

### Pen names `[verified]`

Interned in note.note's string registry:
`com.samsung.android.sdk.pen.pen.preload.` + `FountainPen`,
`ObliquePen` (calligraphy), `InkPen2`, `Pencil2`, `BrushPen`, `Marker4`
(highlighter), `StraightHighlighter`, `Marker3` (marker),
`StraightMarker`.

## note.note (parsed subset) `[verified]`

u32 flex offset, property bitfield, field bitfield, format version, doc
id, file revision, created/modified, **width, height** u32, paddings,
min version, title + body rich-text blobs (u32 size each, skipped).
Flag-gated fields: 0 app name, 1 app version, 2 author, 3 lat/long,
6 template uri, 7 last page index, 9 image id + time, **10 string
registry** (u32 byte size, u16 count, entries u32 id + short string) —
we stop there; later bits hold pen presets, voice data, attachments.
Trailing 32 bytes: sha256 of everything before.

## IR mapping

- One `ir.Page` per `.page`, ordered by `pageIdInfo.dat`. Bounds
  `(0,0,w,h)`, y down `[verified]`. Units are abstract canvas units;
  Samsung's own PDF export maps the page's **short edge to A4's 210 mm**,
  so `point_scale = 595.276 / min(w,h)` `[inferred from the reference
  renderer; matches the samples' proportions]`. Pageless notes are one
  very tall page.
- One `ir.Layer` per layer (name + visibility carried).
- Channels: raw PRESSURE (clamped 0–1); TIMESTAMP `(t-t0)/1000` s
  `[inferred ms]`; TILT_ALTITUDE `= π/2 − tilt`, TILT_AZIMUTH
  `= orientation − π/2` `[inferred conversion]`.
- Tools: pen name → `TOOL_FAMILY` (FountainPen→PEN, InkPen2→BALLPOINT,
  Pencil2→PENCIL, ObliquePen→CALLIGRAPHY, BrushPen→BRUSH,
  Marker3/4 + Straight* → MARKER/HIGHLIGHTER); tool type 4 or the
  eraser bit → ERASER; `NativeTool("sdocx", pen_name, {...})` keeps pen
  size / tool type / fixed flags / advanced settings for round-trips.
- Colour: BGRA bytes → `ir.Color` + alpha as appearance opacity.
  Highlighter/marker families render as underlay + DARKEN blend when
  translucent (the reference emits PDF `Multiply` `[inferred]` — pick
  one when app output is available).
- Width `[inferred]`, mirroring the reference renderer's
  approximation of the app: rendered width
  `= eff_size × clamp(pressure, 0.4, 0.7)` per point (WIDTH channel,
  STROKED_VARIABLE) for pressure-sensitive pens; constant
  `0.45 × eff_size` otherwise; `eff_size = 2.5 × pen_size` for
  highlighter-likes, else `pen_size`. Raw channels are always kept, so
  `--fidelity raw`/`native` don't depend on this model.

## Not parsed / open questions

1. Text boxes (typed text is a Shape+TextCore tree with rich spans),
   images, shapes, lines — skipped by size. Text extraction would be
   the next win.
2. Embedded PDFs (`media/*@pdf_*.pdf` + per-page placement rects in the
   page header) — two corpus samples are annotated PDFs; we currently
   render only the ink. IR has `PdfBackground`; wiring it needs the
   page-header PDF item layout.
3. Timestamp scale when "millisecond mode" is unset; meaning of stroke
   field bit 4 (u32) and property bits 6/7.
4. True app rendering (pressure→width curve, pencil/brush textures,
   highlighter blend) — hardware-deferred; the clamp model above is the
   reference renderer's approximation.
5. `.sdoc` (older Samsung Notes) and encrypted/locked documents are out
   of scope; `end_tag.bin` carries an encryption-info block we ignore.
6. `.spi` page raster caches (could serve as a poor-man's oracle for
   overlay diffs — format unknown).

## Changelog

- 2026-07-09: initial spec + stdlib reader (strokes, layers, string
  registry, end tag), from sdocx2pdf (MIT) + three public samples;
  synthetic fixture generator; corpus-gated tests.
