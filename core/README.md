# inkterop core

Cross-platform Python engine. Reads the reMarkable desktop app's local cache
(read-only, native xochitl v6 format), renders faithful PDFs, and mirrors the
whole library into a folder (default: iCloud Drive/reMarkable).

```sh
uv run inkterop ls                    # list library
uv run inkterop render "My Notebook"  # render one document
uv run inkterop mirror                # one incremental mirror pass
uv run inkterop watch                 # continuous (what the daemon runs)
./launchd/install.sh                   # install the macOS watch daemon
```

Config: `~/.config/inkterop/config.toml` (created on first run) — page-size
normalization, pen style, and mirror scope. Status for UI shells:
`~/.config/inkterop/status.json`.

Notes:
- The reMarkable desktop app must be running for the cache to receive cloud
  changes; add it to Login Items.
- Geometry: strokes are stored in display orientation in a 1620x2160-unit
  Paper Pro canvas; "adjustable page height" grows y. Uniform mode fits each
  page onto a fixed page size (default Letter), which fixes the variable page
  heights of landscape exports.
- Pens: "faithful" style draws each stroke at the device-computed per-point
  width stored in the file (see ../docs/format-notes.md); highlighters use
  their stored RGB at full brightness beneath the ink; page templates
  (dots/lines/grid) are drawn across the full grown page. "rmc" style keeps
  the legacy community formulas for comparison.
- Annotated PDF/EPUB base-page merge is not implemented yet (handwriting-only
  render); notebooks are the default scope.
