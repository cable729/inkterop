# Saber (.sba / .sbn2) format

Status: **read/write support for ink, typed text and image assets**. Ink verified against a Mac
Saber export (format v19, 2026-07-09; fixture
`core/tests/fixtures/saber/saber-mac-pens-text.sba`). Saber
(saber-notes/saber) is open source (GPL-3.0) with an intentionally open
format — our reader is an independent implementation from the BSON spec
and observed files; long-term the better lane is contributing IR export
upstream.

## Container `[verified]`

- `.sba` — zip: `main.sbn2` (+ asset files for images).
- `.sbn2` — one BSON document (bsonspec.org):

| Key | Meaning | Confidence |
|---|---|---|
| `v` | format version (19 observed) | `[verified]` |
| `ni` | next available image object ID | `[verified schema]` |
| `b` | background? (null observed) | `[unknown]` |
| `z` | pages array | `[verified]` |
| `z[].w`, `z[].h` | page size in canvas units (1000x1400 observed) | `[verified]` |
| `z[].s` | strokes | `[verified]` |
| `z[].q` | Quill delta rich text (`insert` runs) | `[verified]` |
| `z[].i` | movable image objects | `[verified schema]` |
| `z[].b` | background image object | `[verified schema]` |

## Stroke `[verified]`

| Key | Meaning |
|---|---|
| `ty` | tool name string: `fountainPen`, `Pencil`, `Highlighter`, … |
| `pe` | pressure-enabled bool |
| `c` | ARGB uint32 (alpha 0x65 on highlighter → translucent) |
| `s` | base size (5 pens, 50 highlighter observed) |
| `sm` | smoothing factor? `[inferred]` |
| `i` | page index |
| `p` | point array: binary structs, little-endian float32 — (x, y) when `pe=0`, (x, y, pressure 0-1) when `pe=1` |

## IR mapping

Tool names → families (`TOOL_FAMILY` in
`formats/saber/reader.py`); ARGB alpha → appearance opacity; raw
PRESSURE channel preserved; appearance is constant-width at the base
size — Saber's own pressure→width curve is GPL code we don't
reimplement, so `exact` fidelity is approximate `[inferred]` while
`raw`/`native` are faithful. `point_scale = 595/1000` (canvas → points)
`[inferred]` from page proportions.

## Writer (2026-07-09)

`formats/saber/writer.py` emits `.sbn2`/`.sba` (zip with `main.sbn2`) as
the exact inverse of the reader: observed v19 top-level constants
(`v/ni/b/p/l/lt/z/c`) and per-stroke fields (`shape/p/i/ty/pe/c/s/sm/sp`)
`[verified against the fixture]`; ARGB packed as unsigned-valued int64
when its high bit is set, as in the app's BSON export.
NativeTool round-trips `ty/s/sm/pe` verbatim; foreign strokes map families
via `FAMILY_TOOL` and take size from appearance/WIDTH median. Texts are
re-emitted as Quill `{"insert": ...}` runs (trailing `\n` appended per
Quill's invariant). RAW fidelity accepted — pressure is the one raw
channel Saber stores; speed/tilt drop. `validated=False` pending the
Saber Mac app-open check (docs/validated-writes.md).

## Open questions

1. Pressure→rendered-width curve (ask upstream or measure from PDF
   exports; overlay-diff once corpus case 16 exists on iPad).
2. Background templates; native app-open validation for image assets — corpus case 11.
3. Eraser & shape-pen encodings — cases 08/09.
4. Exact canvas-unit→point scale; Saber's PDF export page size.

## Changelog

- 2026-07-09: initial spec + reader from a controlled Mac export (v19).
- 2026-07-09: writer (inverse mapping, BSON encoder), round-trip tested;
  ships behind `--experimental`.

## Image assets (2026-10-10)

Wire-format facts checked against Saber revision
[`f143d84`](https://github.com/saber-notes/saber/tree/f143d84b46cb6faf795b13c00aee6d31f69e5da9):
`editor_image.dart`, `png_editor_image.dart`, `data/editor/page.dart`,
`editor_core_info.dart`, and the `.sba` import path in `file_manager.dart`.
Only the serialization schema is used; no GPL rendering/pressure algorithms
are copied or ported. Confidence: **schema checked, round-trip tested;
new files not yet opened in the target app**.

An image record in `z[].i` has:

| Key | Meaning |
|---|---|
| `id`, `i` | unique image object ID and zero-based page index |
| `a`, `e` | asset index and extension (`.png`, `.jpg`, etc.) |
| `x`, `y`, `w`, `h` | destination rectangle in page canvas units |
| `sx`, `sy`, `sw`, `sh` | source-pixel rectangle (crop) |
| `nw`, `nh` | natural pixel dimensions |
| `v` | invertible in dark mode (writer uses false to preserve colors) |
| `f` | background fit (1 = contain) |

`.sba` contains `main.sbn2` and `main.sbn2.0`, `main.sbn2.1`, etc.
Identical asset bytes are deduplicated; image IDs remain distinct. `ni`
is larger than the IDs emitted. Each image's destination rectangle is
rebased by page bounds, including after `--pages` selection. Image
backgrounds use `z[].b` with the same schema.

Standalone `.sbn2` embeds binary assets in the legacy-compatible top-level
`a` array so copying a single file does not lose its images. The reader also
accepts adjacent `.sbn2.N` sidecars, old inline image bytes, and indexed ZIP
assets. Missing referenced bytes raise an error. Tests assert archive
member names, field meanings and page-local geometry independently of
the reader/writer round-trip assertions.
