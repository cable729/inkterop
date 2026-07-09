# remarkable-interop — agent notes

Monorepo making Caleb's reMarkable Paper Pro interoperate with everything
else. Read `docs/ROADMAP.md` for status/next steps, `docs/format-notes.md`
before touching the renderer, `docs/research.md` for the ecosystem survey.

## Layout

- `core/` — Python engine (`rminterop`, uv project). Library reader, PDF
  renderer, incremental mirror to iCloud Drive, CLI, launchd daemon.
- `device-mods/` — XOVI/qmd kit for the landscape fixed-page-size mod +
  warranty-safe rollback. Time-sensitive: see ROADMAP Phase 0.
- `macos/` — (not started) SwiftUI menu-bar shell around `rminterop watch`.
- `docs/` — roadmap, research, reverse-engineered format notes.

## Commands

```sh
cd core
uv run pytest -q                 # tests (synthetic cache; no device needed)
uv run rminterop ls              # list the real library
uv run rminterop render "Name"   # render one doc
uv run rminterop mirror          # incremental mirror pass
./launchd/install.sh             # (re)install the watch daemon
device-mods/verify-export.sh f.pdf  # page-size uniformity check
```

## Hard-won facts (do not rediscover)

- **Source of truth**: the desktop app's cache at
  `~/Library/Containers/com.remarkable.desktop/Data/Library/Application Support/remarkable/desktop/`
  is a plain xochitl-format mirror (`UUID.metadata/.content`, `UUID/*.rm` v6).
  It only updates while the reMarkable desktop app runs. **Never write to it.**
- **Geometry**: Paper Pro strokes are stored in DISPLAY orientation — no
  rotation for landscape (rM2-era tools assume otherwise and are wrong here).
  Canvas 1620x2160 units (landscape 2160x1620); y grows past nominal height
  ("adjustable page height"); export scale ≈ 685pt/2160u. Validated ~2%
  against official exports.
- **Pen widths**: every point stores a device-computed rendered width; true
  width = `point.width / 4` units. Never layer rmc's pressure/speed formulas
  on top (double-counts → blobby calligraphy, gray-thin ballpoint). Details +
  official-export PDF-op analysis: `docs/format-notes.md`.
- **Cloud writes are risky** (official reMarkable bulletin about third-party
  corruption): read via desktop cache; write only via ddvk/rmapi after a
  fresh `rmapi` backup.
- **Device rollback ordering** (warranty): run `device-mods/rollback.sh`
  BEFORE disabling developer mode — xovi-tripletap leaves a systemd unit on
  the root partition that survives factory reset.

## State that lives outside the repo

- Mirror output: `iCloud Drive/reMarkable/` (+ `.rminterop-state.json`).
- Config: `~/.config/rminterop/config.toml`; status for UI shells:
  `~/.config/rminterop/status.json`; daemon log: `~/.config/rminterop/watch.log`.
- launchd agent: `com.rminterop.watch` (`launchctl list | grep rminterop`).

## Style

Python 3.12, uv, no heavy deps (rmscene/reportlab/pikepdf/watchdog).
Prefer empirical validation against official exports over trusting
community-tool formulas — that approach found every fidelity bug so far.
