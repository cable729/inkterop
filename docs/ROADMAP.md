# Roadmap

Full original plan: local planning notes
(approved 2026-07-08). Caleb's goals: (1) iPad interop without leaving
reMarkable land, (2) fixed standard page size in landscape, (3) pen strokes
that export the way they look on-device, (4) hands-off ~1-2 min sync,
(5) notes readable in a normal PDF app, auto-updating.

## Phase 0 — XOVI landscape experiment on-device  ⏳ WAITING ON CALEB
**Time-critical**: the Paper Pro ships for warranty repair (ghosting) around
2026-07-12. Kit is ready in `device-mods/` — follow its README checklist:
root (dev mode wipes; cloud restores) → remagic installs XOVI → install
`disableInfiniteScroll` + `createPagesPaperProSize` qmds → THE pivotal test:
does clamping the canvas make landscape exports uniform? → iterate qmd if
not → `rollback.sh` BEFORE disabling dev mode → ship.
Also while rooted: `grab-corpus.sh` (test data), pull
`/usr/share/remarkable/templates/*.svg` (exact template art — our dot
spacing is approximated), screenshot pen strokes (fidelity references),
test whether ghostbuster's full refresh clears the ghosting (repair-claim
diagnostic). If a replacement unit returns, re-run `install.sh`.

## Phase 1 — Python core  ✅ DONE (2026-07-08)
Library reader, faithful renderer (see `docs/format-notes.md`), incremental
mirror to iCloud Drive (34 notebooks: 68s full, <1s incremental), TOML
config, CLI, launchd watch daemon (installed and running). Caleb confirmed
output "looks really correct".

Known gaps / follow-ups:
- Pencil / mechanical pencil / paintbrush / shader alphas are best-guess —
  not exercised by the validation notebook. Calibrate with an ops-diff against
  an official export of a notebook using them (method in format-notes).
- Template art approximated (dots ✓, lines/grid rough); replace with real
  template SVGs from the device (Phase 0 grab).
- Annotated PDF/EPUB base-page merge not implemented (`scope.pdfs/epubs`
  default false). Plan: pikepdf overlay of rendered annotations onto base.
- `normalize = "paginate"` option (split tall grown pages into multiple
  fixed pages) not implemented; current default scales to fit.
- Typed-text blocks in notebooks render nothing yet (rmscene exposes them;
  rmc's text layout is a reasonable starting point).

## Phase 2 — macOS menu-bar app  ◻ NOT STARTED
SwiftUI `MenuBarExtra` shell that supervises a bundled `rminterop watch`
(PyInstaller binary), shows `~/.config/rminterop/status.json`, Settings
window editing the TOML, `SMAppService` login item. Keep ALL logic in
`core/` — Windows/Linux tray shells reuse the same core + status protocol.
(Windows: desktop app has an equivalent cache; Linux: no desktop app, use
rmapi read-only pull as the library source.)

## Phase 3 — Import lane + iPad trial  ◻ NOT STARTED
- `rminterop send <pdf> [--folder X]` via ddvk/rmapi (`mkdir`+`put`),
  ALWAYS preceded by `rmapi` backup (official corruption warning).
- `rminterop send --editable` via drawj2d 1.4+ (PDF/SVG → .rmdoc native
  editable ink, full Paper Pro color).
- iPad app trial order (rationale in `docs/research.md`): 1. Saber (only
  open-format iPad app; verify writing feel), 2. Nebo (best feel; one-way
  SVG out), 3. OneNote (best automation: Graph API `?includeinkML=true`).
  GoodNotes/Notability rejected — closed/uncracked (GoodNotes) or format
  being replaced mid-crack (Notability).
- Stretch: Saber `.sbn2` ↔ `.rm` bidirectional bridge (both formats open).

## Phase 4 — Later
Windows/Linux shells; app distribution (notarization/updates); OneNote
InkML puller; exact template art; publish for other reMarkable users.
