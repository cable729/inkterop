# inkterop

[![CI](https://github.com/cable729/inkterop/actions/workflows/ci.yml/badge.svg)](https://github.com/cable729/inkterop/actions/workflows/ci.yml)
[![Docs](https://github.com/cable729/inkterop/actions/workflows/docs.yml/badge.svg)](https://cable729.github.io/inkterop/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Documentation: [cable729.github.io/inkterop](https://cable729.github.io/inkterop/)**

A universal converter between handwritten-note formats — e-ink devices,
note apps, and display formats — plus the reMarkable → PDF mirror it grew
out of.

No cross-app handwriting converter existed before this project: every app
speaks its own (usually undocumented) format, and "interop" meant flat PDF
export. inkterop reads native ink — the actual pen strokes, with
per-point width/pressure/tilt where the source stores them — into a
documented intermediate representation ([docs/ir.md](docs/ir.md)) and
writes it back out.

```sh
cd core
uv run inkterop convert notes.goodnotes notes.xopp   # GoodNotes -> Xournal++, editable ink
uv run inkterop convert "My Notebook" out.pdf        # reMarkable library doc -> PDF
uv run inkterop convert notes.sba raw.json --fidelity raw   # raw per-point pen data
uv run inkterop inspect mystery.note                 # what's inside?
```

## Format support

| Format | Read | Write | Notes |
|---|---|---|---|
| reMarkable v6 (`.rm`, Paper Pro) | ✓ | — (M2) | reference implementation; output validated ~2% against official exports |
| GoodNotes 6 (`.goodnotes`) | ink+color | — | reverse-engineered here — [docs/formats/goodnotes.md](docs/formats/goodnotes.md) |
| Notability modern (`.ntb`) | ✓ | — | FlatBuffers format reverse-engineered here |
| Notability legacy (`.note`) | ✓ | — | native pages, editable ink, positioned images |
| Saber (`.sba`/`.sbn2`) | ✓ | experimental | pages, ink, text and image assets; raw pressure preserved |
| Supernote (`.note`) | raster | — | via supernotelib; vector ink is an open RE target |
| Xournal++ (`.xopp`) | ✓ | **✓** | app-open validated (Xournal++ 1.3.5) |
| Nebo/MyScript (`.nebo`) | container | — | BINK ink codec under active RE |
| InkML (W3C) | ✓ | ✓ | raw-fidelity interchange (pressure/tilt channels) |
| SVG | — | ✓ | filled-outline variable width, blend modes |
| PDF | — | ✓ | quirk-exact port of the validated renderer |
| IR-JSON (`.json`) | ✓ | ✓ | the full IR, lossless |

Three fidelity modes per conversion: `exact` (the source app's look),
`native` (the target restyles semantically), `raw` (per-point pen
dynamics). See [docs/ir.md](docs/ir.md).

### Notability to Saber

```sh
cd core
uv run inkterop convert lecture.note lecture.sba --experimental
```

Legacy `.note` files now use their stored canvas width and native paper
geometry to produce separate pages. Embedded pictures retain their position,
size and pixels; `.sba` packages them as Saber image objects. Crossing ink
is split at page boundaries with its colors and widths retained.

Use `.sba` for import into Saber. `.sbn2` also supports self-contained
inline images. The writer remains experimental until the new page/image
path has been checked in the target app. PDF-backed Notability layouts,
typed text and modern `.ntb` page/image decoding are not covered by this
legacy-reader change. Details: [Notability](docs/formats/notability.md)
and [Saber](docs/formats/saber.md).

## Repo layout

- **`core/`** — `inkterop`, the Python engine (uv project): the IR,
  format readers/writers, PDF/SVG renderers, the `convert`/`inspect` CLI,
  and the original mirror pipeline — reads the reMarkable desktop app's
  local library cache and renders faithful PDFs into iCloud Drive via a
  launchd daemon.
- **`tools/re/`** — reverse-engineering toolkit (protobuf wire walker,
  Apple-framed-LZ4 decoder, container inventory) used for the format work.
- **`macos/`** — (planned) SwiftUI menu-bar shell supervising the engine.
- **`device-mods/`** — XOVI/qmd kit for the Paper Pro: fixed landscape
  page size, with scripted install and warranty-clean rollback.
- **`docs/`** — the IR spec, per-format reverse-engineering docs with
  `[verified]/[inferred]/[unknown]` confidence markers, RE methodology,
  corpus protocol, write-safety policy, roadmap.

## Why

reMarkable's own PDF export renders pens differently than the device,
landscape pages grow vertically so exports have random page sizes, and
every note app locks ink inside its own format. The desktop apps, however,
keep local caches and produce native-format exports — everything here
builds on reading those (read-only, zero cloud risk), understanding them
empirically, and validating against each app's own rendering.

## Setup

Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
cd core
uv run pytest -q        # 80 tests; no device needed
uv run inkterop --help
```

## Start here

- [docs/HANDOFF.md](docs/HANDOFF.md) — current state, in-flight work, how
  to pick up each thread
- [docs/ROADMAP.md](docs/ROADMAP.md) — phases, status, known gaps
- [docs/ir.md](docs/ir.md) — the intermediate representation + new-reader
  checklist
- [docs/reverse-engineering.md](docs/reverse-engineering.md) — methodology,
  sample ethics, GPL boundary policy
- [docs/research.md](docs/research.md) — ecosystem survey
- [CLAUDE.md](CLAUDE.md) — agent quick-start: commands, gotchas, external
  state

## License

Code: MIT. Documentation: CC BY 4.0. Self-generated test fixtures: CC0.
`device-mods/vendor/` is vendored GPL-3.0 (see [NOTICE](NOTICE)).
Format facts credited to prior work in each format doc.
