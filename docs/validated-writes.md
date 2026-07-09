# Validated-writes policy

Why some writers are gated behind `--experimental` and how a writer earns
its way out.

## The rule

`FormatWriter.validated: bool` (`core/src/inkterop/formats/base.py`) is
part of every writer's contract. `convert()`
(`core/src/inkterop/convert.py`) checks it before writing anything:

```python
if not writer.validated and not experimental:
    raise ConvertError(
        f"the {writer.format_id} writer is not validated against the "
        f"target app yet; pass --experimental to use it anyway"
    )
```

`validated = False` is the default posture for any new native-format
writer. A writer only flips to `True` after a **documented manual check**
that the *target application* — not just our own reader — opens the
writer's output without errors or content loss, on a specific app version,
recorded in the checklist table below.

**Open formats and our own format are the exception.** A writer for a
format we don't need a foreign app to validate against (IR-JSON — it's our
format; xopp/InkML — open, public specs) can be `validated = True` on the
strength of round-trip test coverage alone, since there's no closed target
app whose behavior we can't inspect. Every writer in this repo so far
falls into that bucket; see the status table below. The policy exists for
when native-format writers (reMarkable `.rm`/`.rmdoc`, `.note`, GoodNotes)
land, where "our reader reads it back fine" is not evidence the *real app*
will.

## Why this matters

Two things make a bad native-format write worse than a bad read:

- **Official reMarkable bulletin**: reMarkable has publicly warned that
  third-party cloud-write tools have corrupted user libraries. Writing is
  categorically riskier than reading — a malformed read just fails to
  parse; a malformed write can corrupt a document the source app then
  chokes on, or silently drops content the user doesn't notice until
  later.
- **Cloud sync amplifies it.** A bad local write is recoverable from a
  backup. A bad write into a directory that syncs to a cloud service (or
  gets picked up by a watch daemon and propagated) can overwrite the good
  copy everywhere before anyone notices.

## Deny-list: never write into source-of-truth / cloud-synced dirs

`convert.py:_forbidden_roots()` currently returns the reMarkable desktop
app's cache directory (via `library.default_cache_dir()` — the same path
documented in the top-level `CLAUDE.md` as "source of truth… **never write
to it**"). `convert()` resolves the output path and refuses to write
inside any forbidden root unless `force=True` is explicitly passed:

```python
out_resolved = out_path.resolve()
for root in _forbidden_roots():
    if root and out_resolved.is_relative_to(root) and not force:
        raise ConvertError(
            f"refusing to write into source-of-truth dir {root}"
        )
```

This list must grow as native writers land and as other apps' caches or
cloud-synced directories become plausible (accidental) output targets —
e.g. a future GoodNotes/Notability writer should add those apps'
containers to `_forbidden_roots()`, and the iCloud Drive mirror output
directory (`iCloud Drive/reMarkable/`) is a candidate too once anything
other than the mirror engine itself could target it.

## Validation checklist template

Use this table (append a row per writer × app-version combination) when
flipping a writer from `False` to `True`, or re-validating after a target
app update:

| Field | What to record |
|---|---|
| Writer / format | e.g. `xopp`, `remarkable (.rmdoc)` |
| Target app + version | the app that must open the output, and its exact version |
| Fixture set used | which `core/tests/fixtures/<format>/` files (or corpus cases) were written and opened |
| Open-check result | did the target app open the file without an error dialog, a repair prompt, or silently dropped content? |
| Round-trip re-read result | does our own reader, reading the just-written file back, reproduce the same IR (within expected fidelity loss)? |
| Reviewer | who ran the check |
| Date | when |

A writer failing any row of a re-check (e.g. after a target-app update
changes its parser) should be flipped back to `validated = False` and the
failure recorded here, not silently left `True`.

### Completed checks

| Writer | Target app | Fixtures | Open check | Round-trip | Reviewer | Date |
|---|---|---|---|---|---|---|
| xopp | Xournal++ 1.3.5 (Mac) | GoodNotes mixed-pens fixture; reMarkable "Getting started" p1-3 (307 strokes, incl. dots); Saber pens+text fixture | PASS — all three opened without errors; colors/widths/highlighter translucency correct. First attempt FAILED on reMarkable ("Wrong count of points (2)"): Xournal++ rejects single-point strokes; writer now emits dots as 0.001pt micro-segments (`test_single_point_stroke_becomes_valid_segment`). | PASS (`core/tests/test_xopp.py`) | maintainer (visual) + Claude (structural) | 2026-07-09 |
| excalidraw | `@excalidraw/excalidraw` 0.18.0 (official npm package — the engine excalidraw.com is built on), `loadFromBlob` file-open path + `exportToSvg` | scribble.excalidraw round-trip; `fineliner-pencil-colors.rm` → .excalidraw | PASS — both load with zero dropped elements; `restore()` rewrote only bookkeeping fields (version/versionNonce/updated/boundElements). First attempt rendered ~8× fat: `strokeWidth` is not the on-canvas thickness (perfect-freehand law, measured — `docs/formats/excalidraw.md`); writer now encodes widths through the inverse law. Visual match vs the golden-validated renderer confirmed; known residual: per-point pencil ALPHA flattens to median element opacity. | PASS (`core/tests/test_excalidraw.py`, incl. `test_width_law_round_trip`) | Claude (browser session, package oracle) | 2026-07-09 |

## Current status

| Format | Writer | `validated` | Basis |
|---|---|---|---|
| PDF | `render/pdf.py: PdfWriter` | `True` | Not a foreign app format to break — PDF renderers are permissive by design; drawing behavior is a quirk-exact port of the geometry validated ~2% against reMarkable's own official export (`docs/formats/remarkable.md`). |
| SVG | `render/svg.py: SvgWriter` | `True` | Open, universally-supported format; no foreign-app round-trip needed. |
| InkML | `formats/inkml.py: InkmlWriter` | `True` | Open W3C standard; round-trip covered by tests (`core/tests/test_inkml.py` — see `docs/formats/inkml-mapping.md`). |
| xopp | `formats/xopp/writer.py: XoppWriter` | `True` | Open, documented Xournal++ format; round-trip covered by `core/tests/test_xopp.py`; **app-open check passed** (see checklist row below). |
| IR-JSON (`.json`) | `formats/irjson.py: IrJsonWriter` | `True` | Our own format; round-trip covered by tests. |
| Saber (`.sba`/`.sbn2`) | `formats/saber/writer.py: SaberWriter` | **`False`** | First native-app writer. Round-trip covered by `core/tests/test_saber.py` (synthetic + fixture); awaiting the Saber Mac app-open check. Saber is open source, so the risk profile is milder than closed apps, but the app's loader — not our reader — is still the authority. |
| reMarkable (`.rm`/`.rmdoc`) | `formats/remarkable/writer.py` | **`False`** | Same-format round-trip int-exact on all four device fixtures (`core/tests/test_remarkable_writer.py`); awaiting desktop-app UI import check of a written `.rmdoc` — the only sanctioned validation path (never the cache/cloud). |
| Supernote (`.note`) | `formats/supernote/writer.py: SupernoteWriter` | **`False`** | Raster-only writer; write->read crosses an independent parser (supernotelib) with pixel-bbox asserts (`core/tests/test_supernote_writer.py`). Awaiting a real device / Partner-app open check. |
| GoodNotes (`.goodnotes`) | `formats/goodnotes/writer.py: GoodnotesWriter` | **`False`** | Encoder inverses property-tested; synthetic + Mac-export fixture write->read round-trips (`core/tests/test_goodnotes_writer.py`). Awaiting the GoodNotes Mac app-import check — closed app, loader strictness unknown (minimal member set, raw `bv4-` LZ4 frames). |
| Notability (`.ntb`) | `formats/notability/writer.py: NtbWriter` | **`False`** | Synthetic + fixture write->read round-trips and schema-less fbwalk framing checks (`core/tests/test_ntb_writer.py`). Gated on TWO items: the color byte order (R vs G, format-doc open question — written as our reader interprets it, so a swap would show in-app, not in round-trips) and the Notability Mac app-open check. Fallback if .ntb import fails: a legacy `Session.plist` writer (svg2notability precedent). |
| Excalidraw (`.excalidraw`) | `formats/excalidraw.py: ExcalidrawWriter` | `True` | Open MIT format; **app-open check passed** against the official `@excalidraw/excalidraw` 0.18.0 package (see checklist row above) after fixing the freedraw width law. |

All five planned native writers now exist in code; each stays
`validated = False` behind `--experimental` until its checklist row
above documents a real app-open check.

The deny-list now also covers the GoodNotes / Notability / Saber macOS
app containers and the iCloud mirror output directory
(`convert.py:_forbidden_roots()`).

## Changelog

- 2026-07-09: initial policy doc; captured the as-implemented deny-list
  and the five currently-validated writers, none of which are native-app
  formats yet.
