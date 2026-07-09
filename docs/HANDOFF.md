# Handoff — state as of 2026-07-09 (evening)

For anyone (human or agent) picking this repo up. Complements
`ROADMAP.md` (plan), `CLAUDE.md` (quick-start), `ir.md` (the model).

## What exists and works

- **Universal converter (M1, done)**: `inkterop convert IN OUT
  [--fidelity exact|native|raw] [--pages N-M]` + `inkterop inspect`.
  Readers: remarkable, goodnotes, notability (.note legacy AND .ntb
  modern), saber, supernote (raster), xopp, inkml, irjson. Writers: pdf,
  svg, inkml, xopp (app-validated), irjson. 80 tests green
  (`cd core && uv run pytest -q`).
- **The original mirror** (`mirror`/`watch` + launchd daemon) is the
  maintainer's daily driver and untouched behaviorally: golden tests + a whole-library
  A/B (110/110 docs op-identical) prove the IR refactor changed nothing.
- **RE toolkit** `tools/re/` (pbwire, applelz4, inventory) — everything
  the format work was done with.

## Invariants — do not break

1. **Golden tests pin rendering** (`core/tests/golden/*.ops.json.gz`).
   A golden diff means you changed PDF output. Never regenerate
   (`pytest --update-goldens`) to silence a failure without
   understanding it. For renderer changes, also run
   `core/scripts/ab_check.py` snapshot/compare over the real library.
2. **Never write into the reMarkable desktop cache** (source of truth;
   `convert.py` refuses it). Native-format writers ship behind
   `--experimental` until app-open validated (`validated-writes.md`).
3. **GPL boundary**: goodparse (GoodNotes) and Saber are GPL-3.0. Format
   FACTS may be used; their SOURCE must never be read into or ported to
   this MIT repo (`reverse-engineering.md` has the policy and case study).
4. `.note` is two formats (Supernote binary / Notability zip) — registry
   disambiguates via `detect()`; keep readers' detect() mutually
   exclusive.

## In flight

- **Nebo BINK codec RE** — spawned as a background task (container
  already mapped in `formats/nebo.md`; a self-made `.nebo` sample + the
  app's own PDF/SVG exports serve as ground truth).
- **.ntb reader** landed from a parallel session (FlatBuffers noteBundle:
  f16 delta-coded segments, width profiles — `formats/notability/ntb.py`,
  fixture `core/tests/fixtures/notability/scribbles.ntb`).

## Open threads, in value order

1. **iPad corpus (iPad arrives ~2026-07-10)**: corpus cases 16-18
   (`corpus-protocol.md`) — pressure ramp, tilt, Mac/iPad parity. These
   should crack: GoodNotes pressure-pen section-9 columns and pencil
   tilt columns (currently frozen at Apple Pencil defaults pi/6, pi/3),
   Notability legacy `curvesfractionalwidths` mapping, Saber's
   pressure→width curve, BINK dynamics.
2. **GoodNotes labeled pen pass** (Mac, 10 min): one doc per tool named
   after the tool → turns pen-type ids {0,1,2,5} into `[verified]` names
   (`formats/goodnotes.md` § pen-type ids). Also outstanding: page-dims
   field (case 14), shape geometry (case 09), erasers (case 08), paper
   templates.
3. **M2 writers**: reMarkable native ink (rmscene `write_blocks` or
   drawj2d), Notability legacy writer (svg2notability precedent). Policy:
   `validated-writes.md`.
4. **PDF exact-blend pass** (pikepdf `/BM /Darken` + reorder highlights
   above ink) and **filled-outline PDF strategy** — both designed, see
   ROADMAP M2; SVG backend already does true outlines.
5. **Annotated PDF/EPUB base-page merge** (`PdfBackground` exists in the
   IR; renderer hook is `render/pdf.py: _draw_raster`'s sibling).
6. **OneNote** via MS Graph API InkML (we already read InkML) or MS-ISF;
   **Apple Notes** (PKDrawing protobuf); **Samsung .sdocx** (study
   twangodev/sdocx — GPL, facts only).
7. **Typed-text rendering** — TextBlocks exist in the IR (xopp/saber
   readers fill them; reMarkable reader doesn't yet) but no renderer
   draws them.
8. **Supernote vector ink**: supernotelib exposes TOTALPATH as opaque
   bytes — an untouched RE target (`formats/supernote.md`).

## Time-sensitive

- **Paper Pro leaves for warranty repair ~2026-07-12.** Device-mod
  rollback ordering matters: `device-mods/rollback.sh` BEFORE disabling
  developer mode. Fixtures already captured (all 9 pen types covered) —
  no further device dependency for the format work.

## Where things live

- Corpus protocol + case matrix: `corpus-protocol.md`. Third-party
  samples: `corpus/third-party/` (gitignored, `MANIFEST.toml`
  provenance). Self-generated fixtures: `core/tests/fixtures/<format>/`
  (committed, CC0).
- Comparison outputs: a disposable local `inkterop-out/` directory.
- App exports used today live in the maintainer's home directory
  (GoodNotes .goodnotes trio, Notability .ntb, Saber .sba + PDF,
  Nebo trio, Notability PDF).
- External state (config, status.json, launchd, mirror output): see
  `CLAUDE.md` § "State that lives outside the repo".
