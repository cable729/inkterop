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
Library reader, faithful renderer (see `docs/formats/remarkable.md`),
incremental mirror to iCloud Drive (34 notebooks: 68s full, <1s
incremental), TOML config, CLI, launchd watch daemon (installed and
running). Caleb confirmed output "looks really correct".

Known gaps / follow-ups:
- Pencil / mechanical pencil / paintbrush / shader alphas are best-guess —
  not exercised by the validation notebook. Calibrate with an ops-diff against
  an official export of a notebook using them (method in
  `docs/formats/remarkable.md`).
- Template art approximated (dots ✓, lines/grid rough); replace with real
  template SVGs from the device (Phase 0 grab).
- Annotated PDF/EPUB base-page merge not implemented (`scope.pdfs/epubs`
  default false). Plan: pikepdf overlay of rendered annotations onto base.
- `normalize = "paginate"` option (split tall grown pages into multiple
  fixed pages) not implemented; current default scales to fit.
- Typed-text blocks in notebooks render nothing yet (rmscene exposes them;
  rmc's text layout is a reasonable starting point).

## Phase 1.5 — universal converter (M1)  ✅ DONE (2026-07-09)
Generalized the reMarkable-only pipeline into a neutral intermediate
representation so any ink format can convert to any other. See
`docs/ir.md` for the model, `docs/reverse-engineering.md` +
`docs/corpus-protocol.md` for how the undocumented formats were (and will
keep being) decoded, `docs/validated-writes.md` for write-safety policy.

- **IR** (`core/src/rminterop/ir/`): neutral document/stroke model,
  per-point channels (pressure/tilt/width/alpha/…), three-fidelity model
  (exact/native/raw), IR-JSON serialization.
- **Format registry + CLI**: `formats/__init__.py` registry,
  `rminterop convert` / `rminterop inspect`.
- **Readers**: reMarkable (ported onto the IR with zero regression —
  golden byte-for-byte drawing-op tests plus a 110/110 whole-library A/B
  check against the pre-IR renderer), xopp, IR-JSON, InkML, GoodNotes
  (ink + color only, experimental — pen-type field still unlocated, see
  `docs/formats/goodnotes.md`), Supernote (raster-first), Notability
  legacy (zip+plist), Notability modern .ntb (FlatBuffers op log decoded
  2026-07-09, render-validated vs the app's own thumbnail; same encoding
  as the app's local-persistence note blobs — see
  `docs/formats/notability.md`).
- **Writers**: PDF, SVG, InkML, xopp, IR-JSON — all `validated = True`
  per `docs/validated-writes.md` (open formats or our own; no native-app
  writers yet).
- **Golden fidelity harness**: `core/tests/test_golden_remarkable.py`
  pins the validated renderer's drawing ops so the IR refactor can't
  silently regress fidelity.
- **RE toolkit**: `tools/re/{inventory,pbwire,applelz4,fbwalk}.py`.
- **Docs**: `docs/ir.md`, `docs/formats/{remarkable,xopp,goodnotes,
  notability,supernote,inkml-mapping}.md`, `docs/reverse-engineering.md`,
  `docs/corpus-protocol.md`, `docs/validated-writes.md`, all with
  confidence markers on unverified claims.
- **License**: MIT.

## M2 — next
- **GoodNotes**: pen-type/pressure fields (open questions 1–3 in
  `docs/formats/goodnotes.md`) via the Mac-app corpus
  (`docs/corpus-protocol.md` cases 05/07/14/16).
- **Notability**: `curvesfractionalwidths` → stroke mapping
  (`docs/formats/notability.md` open question 1) via corpus case 16;
  .ntb color byte order + edit-op semantics (open questions 4–6) via a
  red-ink / erase-and-redraw corpus case.
- **Native-format writers**, each gated by `docs/validated-writes.md`
  (`validated = False` → `--experimental` until an app-open check is
  recorded): xopp is done; reMarkable via `rmscene`
  `write_blocks`/drawj2d; a Notability writer is a candidate per the
  svg2notability precedent (`docs/formats/notability.md`).
- **PDF fidelity**: pikepdf `/BM /Darken` exact-blend pass (replacing the
  underlay-pass approximation, see `docs/ir.md` Appearance semantics) and
  a filled-outline PDF strategy for variable-width strokes (currently
  approximated with piecewise-constant runs, see
  `docs/formats/remarkable.md`).
- **Annotated-PDF base-page merge** (carried over from Phase 1's known
  gaps): pikepdf overlay of rendered annotations onto the imported base
  page.
- **New formats**: OneNote (MS-ISF decode, or Graph API
  `?includeinkML=true` per `docs/research.md`), Apple Notes, Samsung
  `.sdocx`, Boox.

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
