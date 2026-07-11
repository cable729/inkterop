# remarkable-interop — agent notes

Monorepo: a universal converter between e-ink/note-app formats (reMarkable,
GoodNotes, Notability, Supernote, Xournal++, …) and display formats
(PDF/SVG/InkML), grown out of the maintainer's reMarkable Paper Pro mirror. Read
`docs/ROADMAP.md` for status, `docs/ir.md` before touching the IR,
`docs/formats/<name>.md` before touching a format, `docs/research.md` for
the ecosystem survey.

## Layout

- `core/` — Python engine (`inkterop`, uv project).
  - `ir/` — the neutral ink IR every conversion passes through
    (three-fidelity model: raw channels / exact appearance / semantic tool).
  - `formats/` — one reader/writer pair per format + registry.
    remarkable, xopp (rw), irjson (rw), inkml (rw), goodnotes (r),
    supernote (r, raster-first), notability (r, legacy format).
  - `render/` — pdf.py (reportlab; quirk-exact port of the validated
    renderer), svg.py (filled-outline tessellation), primitives.py.
  - `sync/` — multi-source/multi-sink engine: rules.py (per-doc
    allow/block + output overrides), sources.py (reMarkable cache,
    note-file folders, experimental app containers), sinks.py
    (pdf/svg/png/inkz), engine.py, daemon.py (stdio JSON-RPC for the app).
  - `convert.py`, `library.py`, `mirror.py` (thin wrapper over sync/),
    `config.py`, `cli.py`; `packaging/` — PyInstaller sidecar for the app.
- `app/` — Tauri 2 desktop app (React+TS webview, Rust shell). Spawns the
  core daemon as a sidecar (dev: via uv; release: PyInstaller binary).
  Main window (library browser / convert / activity / settings) + tray.
- `website/` — static landing page, deployed to the Pages root; mkdocs
  docs live under /docs/ (`.github/workflows/docs.yml` assembles both).
- `tools/re/` — reverse-engineering toolkit (pbwire, applelz4, inventory).
- `corpus/` — GITIGNORED. third-party/ = downloaded study samples (never
  redistribute, never vendor into tests). Self-made fixtures go to
  `core/tests/fixtures/<format>/` instead.
- `device-mods/` — XOVI/qmd kit + warranty rollback (Phase 0 done; keep
  for the repair-return reinstall).
- `docs/` — roadmap, IR spec, per-format specs (with
  [verified]/[inferred]/[unknown] confidence markers), RE methodology,
  corpus protocol, validated-writes policy.

## Commands

```sh
cd core
uv run pytest -q                    # full suite incl. golden fidelity tests
uv run pytest --update-goldens      # ONLY after an intentional render change
uv run inkterop ls                 # list the real library
uv run inkterop render "Name"      # render one doc to PDF
uv run inkterop mirror             # incremental mirror pass (rM only)
uv run inkterop sync               # pass over ALL configured sources
uv run inkterop daemon             # stdio JSON-RPC engine (the app's sidecar)
uv run inkterop convert IN OUT [--fidelity exact|native|raw]
uv run inkterop inspect IN [--json]   # parsed-content summary (RE workhorse)
uv run python scripts/ab_check.py snapshot|compare  # whole-library A/B
./launchd/install.sh                # (re)install the watch daemon
python3 ../tools/re/inventory.py f.goodnotes  # first look at unknown files

cd ../app
npm run tauri dev                   # desktop app against the live core (uv)
npm run tauri build                 # release bundle; sidecar must exist:
../core/packaging/build-sidecar.sh  #   freeze the engine for this host
```

## Hard-won facts (do not rediscover)

- **Golden tests pin fidelity.** `tests/golden/*.ops.json.gz` are
  normalized PDF content-stream dumps of real `.rm` fixtures; the ported
  renderer was verified op-identical across all 110 library docs. Never
  regenerate goldens to make a red test green — a golden diff means you
  changed rendering.
- **Source of truth**: the desktop app's cache at
  `~/Library/Containers/com.remarkable.desktop/Data/Library/Application Support/remarkable/desktop/`
  is a plain xochitl-format mirror (`UUID.metadata/.content`, `UUID/*.rm` v6).
  It only updates while the reMarkable desktop app runs. **Never write to
  it** (convert.py refuses it as an output path).
- **Geometry**: Paper Pro strokes are stored in DISPLAY orientation — no
  rotation for landscape (rM2-era tools assume otherwise and are wrong here).
  Canvas 1620x2160 units (landscape 2160x1620); y grows past nominal height
  ("adjustable page height"); export scale ≈ 685pt/2160u. Validated ~2%
  against official exports.
- **Pen widths**: every point stores a device-computed rendered width; true
  width = `point.width / 4` units. Never layer rmc's pressure/speed formulas
  on top (double-counts → blobby calligraphy, gray-thin ballpoint). Details:
  `docs/formats/remarkable.md`. GoodNotes bakes pressure into per-point
  widths the same way (`docs/formats/goodnotes.md`).
- **GPL boundary**: the GoodNotes decoder is an independent implementation
  from *documented format facts*. goodparse (GPL-3.0, in corpus/third-party/
  for samples) must never have code read into or ported to this MIT repo.
  Contribute findings back as issues, not code.
- **.note is two formats**: Supernote (binary, `noteSN_FILE_VER_`) vs
  Notability (zip + `Session.plist`). The registry disambiguates via
  detect(); keep it that way.
- **Cloud writes are risky** (official reMarkable bulletin about third-party
  corruption): read via desktop cache; write only via ddvk/rmapi after a
  fresh `rmapi` backup. Writers ship under the validated-writes policy
  (`docs/validated-writes.md`).
- **Device rollback ordering** (warranty): run `device-mods/rollback.sh`
  BEFORE disabling developer mode — xovi-tripletap leaves a systemd unit on
  the root partition that survives factory reset.

## State that lives outside the repo

- Mirror output: `iCloud Drive/reMarkable/` (+ `.inkterop-state.json`,
  now schema v2: per-doc output lists).
- Config: `~/.config/inkterop/config.toml`; sync rules (app-managed):
  `~/.config/inkterop/rules.toml`; status for UI shells:
  `~/.config/inkterop/status.json`; watcher lock: `watch.lock`; thumbnail
  cache: `~/.cache/inkterop/thumbs/`.
- launchd agent: `com.inkterop.watch` (`launchctl list | grep inkterop`) —
  legacy; the desktop app watches instead when running and offers to
  disable it (both at once is prevented by the watch lock).

## Style

Python 3.12, uv. Dependencies: use good ones freely when they pull their
weight (per maintainer, 2026-07-10 — the old "no heavy deps" rule is
rescinded). Licensing: code MIT, docs CC BY 4.0, self-made fixtures CC0.
Prefer empirical validation against official exports over trusting
community-tool formulas — that approach found every fidelity bug so far.
Format claims in docs carry [verified]/[inferred]/[unknown] markers.
