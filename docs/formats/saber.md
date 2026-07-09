# Saber (.sba / .sbn2) format

Status: **read support for ink + typed text**. Verified against a Mac
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
| `ni` | ? (0 observed) | `[unknown]` |
| `b` | background? (null observed) | `[unknown]` |
| `z` | pages array | `[verified]` |
| `z[].w`, `z[].h` | page size in canvas units (1000x1400 observed) | `[verified]` |
| `z[].s` | strokes | `[verified]` |
| `z[].q` | Quill delta rich text (`insert` runs) | `[verified]` |

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

## Open questions

1. Pressure→rendered-width curve (ask upstream or measure from PDF
   exports; overlay-diff once corpus case 16 exists on iPad).
2. `b` background / templates; images (`.sba` assets) — corpus case 11.
3. Eraser & shape-pen encodings — cases 08/09.
4. Exact canvas-unit→point scale; Saber's PDF export page size.

## Changelog

- 2026-07-09: initial spec + reader from a controlled Mac export (v19).
