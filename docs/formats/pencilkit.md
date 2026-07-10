# Apple PencilKit PKDrawing (.pkdrawing)

Status: **read support, decode complete and oracle-validated.**
PKDrawing is the serialized form of Apple's PencilKit canvas
(`PKDrawing.dataRepresentation()`): Apple Notes, Freeform, Screenshot
markup and third-party PencilKit apps all persist ink this way. Bare
blobs have no official extension — `.pkdrawing` is our convention;
reader: `core/src/inkterop/formats/pencilkit.py` (the standalone
`parse_pkdrawing(bytes)` entry point exists so a future apple-notes
reader can decode blobs extracted from the Notes store).

Provenance: our own reverse engineering (no third-party decoders
consulted). Validated on macOS 26 (Darwin 25.5) PencilKit, container
version 1: a Swift generator/oracle (`corpus/scratch/pkgen.swift`)
builds drawings via the public API, serializes them with Apple's own
framework, and dumps the in-memory readback as ground truth. The decoder
reproduces all 22 corpus cases point-exact — 80 control points across
x/y/t/size/force/azimuth/altitude/opacity/secondaryScale, plus ink ids,
colors and renderBounds; zero mismatches, zero residual bytes.
(Generator build caveat: the binary must embed a `CFBundleIdentifier`
via an `__info_plist` section or PKReplicaManager crashes in
CFPreferences.)

## Container `[verified]`

Magic `77 72 64 f0` (`wrd\xf0`) + u16 LE version (=1), then one plain
protobuf message from offset 6 (parses with zero residue).

## Top-level message

| Field | Type | Meaning | Confidence |
|---|---|---|---|
| 1 | varint | always 0, present even when empty | `[unknown]` (sub-version?) |
| 2 (rep) | 16B | replica UUID table; entry 0 all-zero, entry 1 the writing process's replica | `[inferred]` |
| 3 (rep) | msg {1,2,3} | per-replica clock triple, parallel to field 2 | `[inferred]` |
| 4 (rep) | msg | **ink table** | `[verified]` |
| 5 (rep) | msg | **stroke**, in z-order | `[verified]` |
| 6 | msg f32×4 | cached bounds rect; only on load→resave, safe to ignore | `[inferred]` |
| 7 | msg {1,2,3} | clock summary | `[inferred]` |
| 8 | 16B | drawing/session UUID; round-trips through resave | `[inferred]` |

Empty drawing = fields 1, 7, 8 only.

## Ink message (field 4)

- 4.1: msg of 4×f32 = color **RGBA** `[verified]`
- 4.2: string ink id, e.g. `com.apple.ink.pen|pencil|marker` `[verified]`
- 4.3: varint 3 for all inks tested `[unknown]` (ink version?)
- 4.8: double 0.0 `[unknown]`

Inks are deduplicated per (type, color); strokes reference them by table
index `[verified]`.

## Stroke message (field 5)

| Field | Meaning | Confidence |
|---|---|---|
| 5.1 | 16B stroke UUID — regenerated on foreign edit, NOT stable identity | `[verified]` |
| 5.2 | {1,2,3} CRDT id (seq, replicaIndex, clock) | `[inferred]` |
| 5.3 | {0,1,0} always — parent/predecessor ref? | `[unknown]` |
| 5.4 | varint index into the ink table, written even when 0 | `[verified]` |
| 5.5 | path (below) | `[verified]` |
| 5.6 | msg f32×4 = renderBounds x,y,w,h (ink-dependent outset) | `[verified]` |
| 5.8 | opaque varint (~2^37, nondeterministic yet persisted; round-trips byte-identical) — ignore on read | `[verified opaque]` |

## Path message (5.5) — all `[verified]`

- 5.5.1: 16B path UUID — **stable identity** (survives load/resave/append).
- 5.5.2: double creationDate, CFAbsoluteTime (2001 epoch; +978307200 → unix).
- 5.5.3: varint control-point count.
- 5.5.4: varint **per-point channel bitmask**.
- 5.5.5: varint **constant channel bitmask**.
  Invariants: `5.5.4 | 5.5.5 == 0x7FF` (11 channels), `5.5.4 & 5.5.5 == 0`.
  A channel that varies across points is per-point; otherwise its single
  value lives in the constant block. Extreme case (1-point dot): only
  location is per-point (masks 0x001/0x7FE).
- 5.5.6: **constant block** — constant channels' values concatenated in
  ascending bit order, no padding.
- 5.5.7: **point array** — count × fixed-stride records, each the
  per-point channels concatenated in ascending bit order.

### Channel table (bit → wire → decode)

| Bit | Channel | Wire | Decode |
|---|---|---|---|
| 0 | location | 2×f32 | x, y (typographic points) |
| 1 | timeOffset | f32 | seconds since 5.5.2 |
| 2 | size.width | f32 | as-is |
| 3 | aspect | u16 | size.height = width × v/1000 |
| 4 | ? | u16 | always 0 in corpus `[unknown]` |
| 5 | force | u16 | v/1000 |
| 6 | azimuth | u16 | (2v/65535)·π − π (radians, −π..π) |
| 7 | altitude | u16 | (1 − v/65535)·π/2 (radians; π/2 = perpendicular) |
| 8 | opacity | u16 | 2v/65535 (1.0 stores as 32767 → 0.9999847…) |
| 9 | secondaryWidth | f32 | width × secondaryScale |
| 10 | ? | u16 | always 0 in corpus `[unknown]` |

Encoding quantizes by **truncation**, not rounding, and quantization
happens at `PKStrokePoint` construction — the live API already returns
the quantized values, so decode(encode(x)) equals the oracle exactly.

## IR mapping

- Coordinates are PencilKit canvas units = typographic points at 1×
  `[inferred]` → `point_scale = 1.0`. Single page; bounds = union of
  stroke renderBounds padded by 10 (empty drawing → letter-size
  fallback).
- Channels: force → PRESSURE (clamped to 0–1 per the channel contract;
  UITouch force can exceed 1.0 for Pencil, raw values stashed in
  `extra["pencilkit"]["force_raw"]` when clamping changed anything);
  timeOffset → TIMESTAMP; size.width → WIDTH; azimuth → TILT_AZIMUTH
  (normalized from PencilKit's −π..π to [0, 2π) to match the other
  readers; same angle, 0 = +x axis); altitude → TILT_ALTITUDE (already
  the IR convention); opacity → ALPHA when per-point, else
  `appearance.opacity`.
- PencilKit-only geometry (nib aspect, secondaryScale) rides in
  `NativeTool("pencilkit", ink_id, params)` when constant and in
  `extra["pencilkit"]` when per-point; renderBounds, path UUID and
  creation date (unix) also land in `extra["pencilkit"]`.
- Appearance: `STROKED_VARIABLE` (PencilKit renders width per-point),
  RGBA color, `width=None`, blend NORMAL.
- Ink id → family: pen → PEN, pencil → PENCIL, marker → MARKER (it is a
  translucent wide nib, but composites normally: no underlay, blend
  NORMAL `[inferred]`); not yet observed serialized `[inferred]`:
  monoline → FINELINER, fountainPen → CALLIGRAPHY, watercolor → BRUSH,
  crayon → PENCIL; anything else → UNKNOWN.

## Apple Notes / Freeform

Notes stores drawings inside its group container
(`~/Library/Group Containers/group.com.apple.notes/`), which is
TCC-protected — reading it needs Full Disk Access, and whether the
embedded blobs carry the same `wrd\xf0` container is untested here
`[unknown]`. When an apple-notes reader lands it should import
`parse_pkdrawing` from this module.

## Open questions

0. Partial erases: PencilKit's pixel eraser applies a per-stroke mask
   (`PKStroke.mask`) rather than deleting the stroke — no field decoded
   for it yet, so a partially-erased stroke would render in full. Needs
   a sample drawn + partially erased (`docs/erase-audit.md`).
1. Channel bits 4 and 10 (u16, always 0 — barrel roll / future Pencil
   channels? not settable via public API on macOS).
2. Precise CRDT semantics of fields 3/7/5.2/5.3 (layout known, member
   naming inferred; needs a two-replica merge corpus).
3. Top field 1 (=0), ink 4.3 (=3), ink 4.8 (=0.0) — constants,
   version-ish.
4. Whether Notes/Freeform blobs match this container (TCC-blocked).
5. Writer: feasible (all content fields decoded, opaque 5.8 could be
   synthesized), but PencilKit's tolerance of foreign CRDT fields is
   untested.

## Changelog

- 2026-07-09: format cracked and oracle-validated (22 cases, 80 control
  points, zero mismatches); reader + fixtures + tests landed
  (`corpus/scratch/pencilkit/NOTES.md` has the raw RE log).
