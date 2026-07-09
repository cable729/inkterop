# rminterop core

Cross-platform Python engine. Reads the reMarkable desktop app's local cache
(read-only, native xochitl v6 format), renders faithful PDFs, and mirrors the
whole library into a folder (default: iCloud Drive/reMarkable).

```sh
uv run rminterop ls                    # list library
uv run rminterop render "My Notebook"  # render one document
uv run rminterop mirror                # one incremental mirror pass
uv run rminterop watch                 # continuous (what the daemon runs)
./launchd/install.sh                   # install the macOS watch daemon
```

Config: `~/.config/rminterop/config.toml` (created on first run) — page-size
normalization, pen style, and mirror scope. Status for UI shells:
`~/.config/rminterop/status.json`.

Notes:
- The reMarkable desktop app must be running for the cache to receive cloud
  changes; add it to Login Items.
- Geometry: strokes are stored in display orientation in a 1620x2160-unit
  Paper Pro canvas; "adjustable page height" grows y. Uniform mode fits each
  page onto a fixed page size (default Letter), which fixes the variable page
  heights of landscape exports.
- Pen calibration adapted from rmc (MIT); "faithful" style keeps ballpoint
  ink solid like the device instead of pressure-faded gray.
- Annotated PDF/EPUB base-page merge is not implemented yet (handwriting-only
  render); notebooks are the default scope.
