# Roadmap

Original goals (plan approved 2026-07-08): (1) iPad interop without leaving
reMarkable land, (2) fixed standard page size in landscape, (3) pen strokes
that export the way they look on-device, (4) hands-off ~1-2 min sync,
(5) notes readable in a normal PDF app, auto-updating.

## Phase 0 — XOVI landscape experiment on-device  ⏳ WAITING ON DEVICE
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
running). The maintainer confirmed output "looks really correct".

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

- **IR** (`core/src/inkterop/ir/`): neutral document/stroke model,
  per-point channels (pressure/tilt/width/alpha/…), three-fidelity model
  (exact/native/raw), IR-JSON serialization.
- **Format registry + CLI**: `formats/__init__.py` registry,
  `inkterop convert` / `inkterop inspect`.
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

## M2 — complete in/out push  ✅ LARGELY DONE (2026-07-09)

Landed on the note-apps workstream (18 readers / 12 writers total):

- **Native writers, all five** (each `validated = False` behind
  `--experimental` per `docs/validated-writes.md`, awaiting app-open
  checks — samples staged in `corpus/validate/`): Saber, reMarkable
  `.rm`/`.rmdoc` (int-exact device-fixture round-trips), Supernote
  (raster RATTA_RLE, cross-validated via supernotelib), GoodNotes
  (protobuf/LZ4/tpl encoders), Notability `.ntb` (hand-rolled
  FlatBuffers builder).
- **New readers**: Excalidraw (r/w), generic SVG (re-ingests our own
  SVG output — SVG is now two-way) + Stylus Labs Write, Wacom UIM
  3.0/3.1 (oracle-validated vs Wacom's Apache library), MS-ISF
  (codec-exact vs Microsoft's WPF implementation), **OneNote `.one`**
  (full classic ONESTORE parse + undocumented ink hierarchy), Samsung
  `.sdocx`, Onyx Boox `.note`, **PencilKit `.pkdrawing`** (own novel
  RE, oracle-validated — the Apple Notes ink core), tldraw `.tldr`.
- Per-format specs in `docs/formats/` with confidence markers.

Still open in M2 scope:
- **Writer validation session** (app-open checks → flip `validated`);
  Notability additionally gated on the red-ink color-byte-order corpus
  case; GoodNotes on Mac-import member-set iteration.
- **GoodNotes/Notability corpus deepening**: pen-type/pressure fields
  (cases 05/07/14/16), `curvesfractionalwidths` (case 16).
- **Apple Notes NoteStore plumbing** (TCC-gated) on top of the
  PencilKit decoder; **rnote** (needs drawn samples).
- **PDF fidelity** (`/BM /Darken` exact blend; filled-outline
  variable-width strokes) and **annotated-PDF base-page merge** —
  carried over, unchanged.

## Phase 2 — desktop app (sync control center + converter GUI)  ✅ DONE (2026-07-10)
Shipped bigger than planned: a full Tauri 2 app (`app/`) instead of a
menu-bar-only shell — one codebase gives the main window AND the tray item,
and Windows/Linux builds fall out of the same code. ALL logic stays in
`core/`: the app spawns `inkterop daemon` (stdio JSON-RPC sidecar;
PyInstaller-frozen in release bundles, live uv tree in dev).

- **Core sync module** (`core/src/inkterop/sync/`): multi-source
  (reMarkable cache, note-file folders, experimental GoodNotes/Notability
  container scanners), multi-sink (pdf/svg/png/inkz), per-doc rules
  (allow/block, rename, destination, format) in rules.toml, v2 state with
  v1 migration, watcher pid-lock. `mirror`/`watch` unchanged for launchd.
- **App**: library browser (Finder-style columns / thumbnail grid /
  details list, first-page previews, folder+note sync toggles, output
  overrides), drag-and-drop converter over the full registry, activity
  log, settings, tray with sync controls, legacy-launchd migration.
- **Releases**: `.github/workflows/release.yml` — macOS signed+notarized
  (once Apple secrets land — see `app/RELEASING.md`), Windows/Linux
  community builds; Homebrew cask recipe documented.
- **Website**: `website/` landing page at the Pages root, docs moved
  under `/docs/`.

## Phase 3 — Import lane + iPad trial  ◻ NOT STARTED
- `inkterop send <pdf> [--folder X]` via ddvk/rmapi (`mkdir`+`put`),
  ALWAYS preceded by `rmapi` backup (official corruption warning).
- `inkterop send --editable` via drawj2d 1.4+ (PDF/SVG → .rmdoc native
  editable ink, full Paper Pro color).
- iPad app trial order (rationale in `docs/research.md`): 1. Saber (only
  open-format iPad app; verify writing feel), 2. Nebo (best feel; no
  longer one-way — .nebo BINK ink is now decoded, `formats/nebo/`),
  3. OneNote (best automation: Graph API `?includeinkML=true`).
  GoodNotes/Notability rejected — closed/uncracked (GoodNotes) or format
  being replaced mid-crack (Notability).
- Stretch: Saber `.sbn2` ↔ `.rm` bidirectional bridge (both formats open).

## Phase 4 — Later
Windows/Linux shells; app distribution (notarization/updates); OneNote
InkML puller; exact template art; publish for other reMarkable users.
