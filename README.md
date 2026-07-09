# remarkable-interop

Tools to make a reMarkable Paper Pro play nicely with the rest of the world:

- **`core/`** — `rminterop`, a cross-platform Python engine that reads the reMarkable
  desktop app's local library cache (native xochitl v6 format), renders notebooks to
  faithful PDFs, and mirrors them into iCloud Drive so any PDF app sees an
  auto-updating copy of every note. Also an import lane back into reMarkable
  (plain PDFs via rmapi, *editable ink* via drawj2d).
- **`macos/`** — SwiftUI menu-bar app that supervises the engine (status, settings,
  launch-at-login). Windows/Linux shells planned; all logic lives in `core/`.
- **`device-mods/`** — XOVI/qmd kit for the Paper Pro: fixed page size in landscape
  notebooks (stop the vertical page growth), with scripted install and a
  warranty-clean rollback.
- **`docs/`** — research notes and workflows (iPad interop, formats, sync).

## Why

reMarkable's own PDF export renders pens differently than the device (thin spindly
ballpoint), landscape notebook pages grow vertically so exports have random page
sizes, and the proprietary format discourages using any other note app. The desktop
app, however, keeps a complete local mirror of the library in the documented v6
format — everything here builds on reading that cache (read-only, zero cloud risk).

## Status

Phase 1 (mirror pipeline) is live: the launchd daemon renders every notebook
into `iCloud Drive/reMarkable/` within seconds of the desktop app syncing,
with device-faithful pens, bright highlighters, page templates, and uniform
Letter-size pages. Next: on-device XOVI landscape mod (kit ready in
`device-mods/`), then the menu-bar app and the import lane.

Start here:
- [docs/ROADMAP.md](docs/ROADMAP.md) — phases, status, known gaps
- [docs/format-notes.md](docs/format-notes.md) — reverse-engineered v6/export
  facts (read before touching the renderer)
- [docs/research.md](docs/research.md) — ecosystem survey (tools, modding,
  iPad interop reality)
- [CLAUDE.md](CLAUDE.md) — agent quick-start: commands, gotchas, external state
