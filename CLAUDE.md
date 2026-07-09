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
  - `convert.py`, `library.py`, `mirror.py`, `config.py`, `cli.py`.
- `tools/re/` — reverse-engineering toolkit (pbwire, applelz4, inventory).
- `corpus/` — GITIGNORED. third-party/ = downloaded study samples (never
  redistribute, never vendor into tests). Self-made fixtures go to
  `core/tests/fixtures/<format>/` instead.
- `device-mods/` — XOVI/qmd kit + warranty rollback. Time-sensitive:
  ROADMAP Phase 0 (device leaves for repair ~2026-07-12).
- `macos/` — (not started) SwiftUI menu-bar shell around `inkterop watch`.
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
uv run inkterop mirror             # incremental mirror pass
uv run inkterop convert IN OUT [--fidelity exact|native|raw]
uv run inkterop inspect IN [--json]   # parsed-content summary (RE workhorse)
uv run python scripts/ab_check.py snapshot|compare  # whole-library A/B
./launchd/install.sh                # (re)install the watch daemon
python3 ../tools/re/inventory.py f.goodnotes  # first look at unknown files
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

- Mirror output: `iCloud Drive/reMarkable/` (+ `.inkterop-state.json`).
- Config: `~/.config/inkterop/config.toml`; status for UI shells:
  `~/.config/inkterop/status.json`; daemon log: `~/.config/inkterop/watch.log`.
- launchd agent: `com.inkterop.watch` (`launchctl list | grep inkterop`).

## Style

Python 3.12, uv, no heavy deps (rmscene/reportlab/pikepdf/watchdog/
supernotelib). Licensing: code MIT, docs CC BY 4.0, self-made fixtures CC0.
Prefer empirical validation against official exports over trusting
community-tool formulas — that approach found every fidelity bug so far.
Format claims in docs carry [verified]/[inferred]/[unknown] markers.
