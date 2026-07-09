# Handoff — note-apps complete in/out workstream

Status snapshot, 2026-07-09. Companion to `docs/ROADMAP.md` (M2 section);
this file carries the working detail that doesn't belong in the roadmap.
Like `HANDOFF.md`, it is excluded from the docs website.

## What landed (this branch)

Suite 291 passed / 8 corpus-gated skips; golden render tests untouched.
Registry: 19 readers / 12 writers.

- **Native writers (all `validated=False`, `--experimental`)**: Saber,
  reMarkable `.rm`/`.rmdoc`, Supernote raster, GoodNotes, Notability
  `.ntb`. Each has synthetic + fixture round-trip tests; per-format
  notes in `docs/formats/`, policy rows in `docs/validated-writes.md`.
- **New readers**: Excalidraw (r/w), generic SVG (re-ingests our own
  SVG via `data-rmi-*` — SVG is two-way now), Stylus Labs Write, Wacom
  UIM 3.0/3.1, MS-ISF, OneNote `.one` (full classic ONESTORE parse),
  Samsung `.sdocx`, Onyx Boox `.note`, PencilKit `.pkdrawing`, tldraw.
- **RE findings worth upstreaming as issues**: onenote.rs decodes
  InkPath values as absolute (they are first-order deltas — see
  `docs/formats/onenote.md`); boox-note-optimizer's README calls the
  point timestamps deltas (they are cumulative ms — see
  `docs/formats/boox.md`).

## Validation queue (gates `validated=True`)

Written samples staged in the local `corpus/validate/` (gitignored):
per-format round-trips plus reMarkable→X foreign conversions for Saber
Mac, excalidraw.com, reMarkable desktop (File → Import ONLY — never the
cache), GoodNotes Mac, Notability Mac, Supernote (device, later).
Record results as checklist rows in `docs/validated-writes.md`; iterate
on failures — GoodNotes (member-set tolerance, raw `bv4-` frames) and
Notability (op-envelope unknowns) are the likely iteration targets.
Notability is additionally gated on a red-ink corpus case (color byte
order, `docs/formats/notability.md`).

## Blocked / needs new samples

- **Apple Notes NoteStore reader**: the PencilKit ink core is done
  (`formats/pencilkit.py`, `parse_pkdrawing()`); remaining is
  NoteStore.sqlite plumbing (facts in the MIT apple_cloud_notes_parser,
  see corpus MANIFEST). The Notes group container is TCC-protected —
  needs Full Disk Access or files copied out, plus Pencil-drawn notes
  synced in for real samples.
- **rnote**: gzip+JSON, schema to be learned from self-made samples
  (GPL app — samples only, never the source). Needs a drawn sample.
- **Corpus deepening**: GoodNotes/Notability pressure & pen-type cases
  (corpus-protocol cases 05/07/14/16), Nebo native+SVG export pairs,
  OneNote Windows "Export section" + PDF pair (render-validation gate),
  Samsung first-party cases if hardware becomes available.

## Standing rules

- `cd core && uv run pytest -q` green before every commit; never
  regenerate goldens (a golden diff means rendering changed).
- GPL/AGPL/unlicensed references: format facts only, never read/port
  source; sample *data* from such repos is fine for gitignored corpus
  study (provenance rows in the corpus MANIFEST).
- New tests must pass ubuntu CI: gate macOS-only oracles behind
  `skipif(sys.platform != "darwin")` and third-party samples behind
  corpus-presence skips.
- Writers ship `validated=False` until a documented app-open check.
