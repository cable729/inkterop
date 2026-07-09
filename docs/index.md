# inkterop

**A universal converter between handwritten-note formats** — e-ink
devices, note apps, and display formats.

No cross-app handwriting converter existed before this project: every
app speaks its own (usually undocumented) format, and "interop" meant
flat PDF export. inkterop reads native ink — the actual pen strokes,
with per-point width, pressure, and tilt where the source stores them —
into a documented [intermediate representation](ir.md) and writes it
back out.

```sh
inkterop convert notes.goodnotes notes.xopp        # GoodNotes -> Xournal++, editable ink
inkterop convert "My Notebook" out.pdf             # reMarkable library doc -> PDF
inkterop convert notes.sba raw.json --fidelity raw # raw per-point pen data
inkterop inspect mystery.note                      # what's inside?
```

## Format support

| Format | Read | Write | Notes |
|---|---|---|---|
| reMarkable v6 (`.rm`, Paper Pro) | ✓ | — | reference implementation; [validated ~2% against official exports](formats/remarkable.md) |
| GoodNotes 6 (`.goodnotes`) | ink+color | — | [reverse-engineered here](formats/goodnotes.md) |
| Notability modern (`.ntb`) | ✓ | — | [FlatBuffers format reverse-engineered here](formats/notability.md) |
| Notability legacy (`.note`) | ✓ | — | 2018 zip format, verified alive |
| Saber (`.sba`/`.sbn2`) | ✓ | — | [open BSON format](formats/saber.md); raw pressure preserved |
| Supernote (`.note`) | raster | — | [vector ink is an open RE target](formats/supernote.md) |
| Xournal++ (`.xopp`) | ✓ | **✓** | [app-open validated](formats/xopp.md) (Xournal++ 1.3.5) |
| Nebo/MyScript (`.nebo`) | container | — | [BINK ink codec under active RE](formats/nebo.md) |
| InkML (W3C) | ✓ | ✓ | [raw-fidelity interchange](formats/inkml-mapping.md) (pressure/tilt channels) |
| SVG | — | ✓ | filled-outline variable width, blend modes |
| PDF | — | ✓ | quirk-exact port of the validated renderer |
| IR-JSON (`.json`) | ✓ | ✓ | the full IR, lossless |

Three fidelity modes per conversion: `exact` (the source app's look),
`native` (the target restyles semantically), `raw` (per-point pen
dynamics). The [IR specification](ir.md) defines all three.

## Install

Python 3.12+ and [uv](https://docs.astral.sh/uv/):

```sh
git clone https://github.com/cable729/inkterop
cd inkterop/core
uv run inkterop --help
uv run pytest -q        # 80 tests; no device needed
```

Or via Homebrew:

```sh
brew install cable729/tap/inkterop
```

## How the formats were decoded

Every format spec on this site was produced by empirical reverse
engineering — self-generated sample files, byte-level diffing, and
validation against each app's own rendering — with
`[verified]/[inferred]/[unknown]` confidence markers on every claim.
The [methodology](reverse-engineering.md) covers sample ethics and the
GPL-boundary policy; the [corpus protocol](corpus-protocol.md) defines
the test-case matrix; [validated writes](validated-writes.md) is the
safety policy for writing native formats.

## The reMarkable mirror

inkterop grew out of a reMarkable Paper Pro → PDF mirror, and that
pipeline ships in the same package: `inkterop watch` reads the
reMarkable desktop app's local cache (read-only, zero cloud risk) and
renders faithful PDFs into iCloud Drive as you write. The renderer is
validated op-identical against official exports — pens look the way
they do on-device.

## License

Code MIT · docs CC BY 4.0 · self-made test fixtures CC0. Format facts
are credited to prior work in each format doc.
