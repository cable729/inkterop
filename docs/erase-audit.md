# Erased-stroke audit (2026-07-10)

Question: for every reader, do erased strokes stay in the on-disk file,
and if so does the reader correctly skip them? A reader that misses a
tombstone renders ink the user deleted.

## Method

Point-by-point coverage: parse the calibration page (see
`docs/calibration-pages.md`) with our reader, map every stroke point
into the app's OWN PDF export of the same page, and test for
non-background pixels within a small radius. A stroke with low coverage
is ink we emit that the app does not render. Reproduce with
`core/scripts/erase_audit.py` (run from `core/`;
`INKTEROP_CORPUS=<path>` if corpus/ lives elsewhere). Gotchas the first
run hit: background palettes must exclude ink colors (a yellow
highlighter row polls as "background"), and very faint marks (a Saber
pencil dot) sit below any sane threshold — eyeball crops before calling
a stroke erased.

## Results per format

| Format | Do erased strokes persist in the file? | Reader behavior | Evidence |
|---|---|---|---|
| GoodNotes | **No** — erasing removes the ink record from the page file. The empty field-14=1 re-records are NOT erase tombstones (first read as such; refuted — the app renders those items, and a trial tombstone implementation wrongly dropped 2 visible strokes) | render every ink record (correct) | `[verified]` all 87 calibration ink records render in the app export (86 at audit time; a single-segment pencil dot decoded later the same day and audits clean) |
| reMarkable v6 | **Yes** — deleted strokes remain as CRDT sequence items with `value=None` | skipped (`_collect` only descends Group/Line values) | `[verified]` calibration page holds 1 deleted item; golden renders op-identical to official exports across the 110-doc library |
| Nebo | **Yes** — BINK tombstones (a `-1` word where the stroke was) | skipped since a684a07; tombstones still count in tag indices | `[verified]` 32/32 calibration strokes render in the app export |
| Excalidraw | **Yes** — elements persist with `isDeleted: true` | skipped (`el.get("isDeleted")` guard) | `[verified]` by code + documented format |
| OneNote .one | history lives in the revision store | reader replays revisions then walks only the CURRENT revision's root; objects deleted upstream are unreachable | `[inferred]` — MS-ONESTORE replay strategy; no erased sample yet |
| Xournal++ | eraser strokes are *whiteout ink* (`tool="eraser"`); real deletions rewrite the XML | whiteout kept as ERASER-family ink — matches the app | `[verified]` format semantics |
| Samsung .sdocx | eraser tool-type/flags exist, but zero eraser strokes across all samples (7k+ strokes incl. heavily-edited notes) — erasing appears to remove strokes | ERASER family mapped if ever present | `[inferred]` |
| Notability | erase/move/undo ops in the .ntb op log are UNMAPPED — an edited note may contain superseded strokes we still render | fresh notes verified clean | `[verified]` 70/70 calibration strokes render; edited-note risk stays OPEN (needs an erased corpus case) |
| Saber | erase representation unknown (corpus case 08); no leakage observed | — | `[verified]` 72/72 calibration strokes render (one near-invisible pencil dot false-positives the threshold) |
| Onyx Boox | `stash/` undo history ignored `[verified]`; **risk**: points with no shape record are kept as orphan ink — if erasing removes the shape but leaves points, we resurrect them | orphan branch keeps unmatched points | `[unknown]` — needs an erased Boox sample |
| Supernote | raster-first reader; erases are baked into the bitmaps | n/a | `[verified]` by design |
| PencilKit .pkdrawing | full-stroke erases remove strokes; **partial erases apply a PKStroke mask we do not decode** — partially-erased ink would render in full | mask field not identified | `[unknown]` — flag for the Apple Notes work |
| tldraw | .tldr snapshots store only live records | n/a | `[inferred]` |
| ISF / UIM / InkML / SVG / irjson / .inkz / xopp XML | interchange/display formats — files carry only live strokes | n/a | `[verified]` by design |

## Cross-cutting semantics

ERASER-family strokes (xopp whiteout, rM legacy eraser pen, sdocx
eraser type) are *visible white ink*, not deletions — `ir/defaults.py`
renders them as white variable-width strokes, matching Xournal++ and
the golden-pinned reMarkable renderer. Do not confuse them with
tombstones.

## Open follow-ups

1. Notability erased-note corpus case: erase a stroke in-app, re-export,
   and map the erase op (`docs/formats/notability.md` open question 6).
2. Boox erased sample to settle the orphan-points branch.
3. PencilKit partial-erase mask field (blocked on Apple Notes samples).
4. Saber erase representation (corpus case 08) — expected: stroke
   removal, consistent with the clean coverage run.
