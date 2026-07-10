# The inkterop IR (intermediate representation)

`core/src/inkterop/ir/` is the neutral ink document model every format
converts through: a reader turns a native file into an `ir.Document`, a
writer/renderer turns an `ir.Document` into a native file or PDF/SVG.
N readers + M writers instead of N×M pairwise converters — adding a format
means writing one reader (and optionally one writer), and it immediately
interoperates with everything else.

Everything below is implemented and covered by `core/tests/test_ir.py`,
the per-format round-trip tests, and the golden reMarkable regression
suite unless marked otherwise.

## Module tour

| Module | Contents |
|---|---|
| `ir/model.py` | `Document`, `Page`, `Layer`, `Stroke`, `TextBlock`, `RasterImage`, `Rect`, the four `Background` types |
| `ir/channels.py` | `Channel` enum + per-point units contract, `CHANNEL_RANGE` |
| `ir/style.py` | `Color`, `StrokeAppearance`, `GeometryMode`, `BlendMode`, `LineCap` |
| `ir/tools.py` | `ToolFamily`, `NativeTool`, `ToolRef` |
| `ir/defaults.py` | `default_appearance()` / `restyled()` — semantic restyling for `fidelity=native` |
| `ir/serialize.py` | IR ↔ JSON (`inkterop_ir` version 1) |

Adjacent, not part of the IR itself: `formats/base.py` (the `Fidelity`
enum + `FormatReader`/`FormatWriter` protocols), `formats/__init__.py`
(the registry), `convert.py` (read → IR → write orchestration),
`render/` (PDF/SVG backends consuming the IR).

## Core dataclasses

```
Document(format_id, title, pages, orientation, attachments, metadata, extra)
Page(bounds: Rect, point_scale: float, layers, background, extra)
Layer(strokes, texts, raster, name, visible)
Stroke(x, y, tool: ToolRef, color: Color, channels, appearance, extra)
TextBlock(x, y, text, font_size, color, extra)
RasterImage(data: bytes, format: str, bounds: Rect | None)
```

- `Document.orientation` is a hint (`"portrait"` | `"landscape"`), used
  by renderers to pick blank-page sizes.
- `Document.attachments` maps keys to `bytes` or `Path` (e.g. imported
  PDFs referenced by a `PdfBackground`).
- `Layer.raster` carries bitmap layer content (Supernote's RLE layers);
  strokes and raster can coexist in one layer.
- `Page.strokes()` iterates strokes of *visible* layers only.

## Coordinates & units contract

- Stroke coordinates stay in **source units** — readers do NOT rescale.
  Each page declares `point_scale`: source units → PDF points. Writers
  and renderers apply it (usually rebasing to the page's top-left,
  `(x - bounds.x_min) * point_scale`).
- **Y grows downward** everywhere. Readers from y-up formats must flip.
- `Page.bounds` is in source units and need not start at (0, 0):
  reMarkable pages are x-centered on 0 with y grown past the nominal
  height. Never assume `x_min == 0`.

Examples: reMarkable `point_scale = 685/2160 ≈ 0.3171`; xopp and
GoodNotes coordinates are already points (`point_scale = 1.0`);
Supernote pixels use `595.0 / device_width`.

## Per-point channels

X/Y are implicit (every stroke has them); `Stroke.channels` maps
`Channel -> list[float]`, struct-of-arrays, one value per point, length
equal to the point count (enforced by `Stroke.validate()`).

Units contract — what readers must normalize TO (from
`ir/channels.py`):

| Channel | Units |
|---|---|
| `PRESSURE` | 0.0–1.0 (0 = no contact reported, 1 = max sensor value) |
| `TILT_AZIMUTH` | radians, 0 = +x axis, counterclockwise in page space |
| `TILT_ALTITUDE` | radians, π/2 = perpendicular to surface |
| `SPEED` | source units/second (source-specific magnitude; comparable only within a document) |
| `WIDTH` | rendered stroke width at this point, in the page's source units — the "device already computed it" channel; **never re-derive from pressure when this is present** |
| `ALPHA` | 0.0–1.0 opacity at this point |
| `TIMESTAMP` | seconds since stroke start |

`CHANNEL_RANGE` declares (0,1) bounds for PRESSURE/ALPHA; the rest are
unbounded/source-specific.

## The three-fidelity model

`Fidelity` (`formats/base.py`) names the three layers of information a
stroke carries; `inkterop convert --fidelity exact|native|raw` selects
which one the writer honors.

- **exact** — reproduce the *source app's* rendering. Consumes
  `Stroke.appearance` (populated by readers from observed/RE'd rendering
  behavior). Default.
- **native** — map tools semantically; the *target* restyles them.
  Writers ignore `appearance` and rebuild it from `ToolFamily` via
  `ir/defaults.py: restyled()` (which also strips the `"inkterop"` key
  from `Stroke.extra`).
- **raw** — the per-point event data itself (the channels). Only formats
  that can hold pen dynamics accept it: IR-JSON and InkML. PDF/SVG/xopp
  raise `ValueError` for raw.

Rules for implementers:

- **Readers** populate all three layers when the source has them: raw
  channels, an `appearance` that matches how the source app draws, and a
  semantic `ToolFamily` (+ `NativeTool` for round-trips). A reader that
  can't determine the source rendering leaves `appearance = None`.
- **Writers** must branch on fidelity: exact ⇒ use `appearance` (falling
  back to `default_appearance()` when it is `None`); native ⇒ `restyled()`
  every stroke; raw ⇒ either serialize the channels losslessly or raise.
- IR-JSON is the exception: it always carries all three layers, so its
  writer ignores the fidelity knob.

## Tool taxonomy

`ToolRef(family, native)`:

- `ToolFamily` — the neutral vocabulary a *foreign* writer consumes:
  `pen, ballpoint, fineliner, pencil, mechanical_pencil, marker,
  highlighter, shader, brush, calligraphy, eraser, unknown`.
- `NativeTool(format_id, tool_id, params)` — the source format's exact
  tool record, untouched, so a *same-format* writer can round-trip
  perfectly (e.g. reMarkable stores the rmscene `Pen` enum value plus
  `color`/`color_rgba`/`thickness_scale` in `params`).

Extension rules: map to the closest existing family; use `UNKNOWN` (not
a creative guess) when nothing fits; never extend the enum for one
format's exotic tool — that's what `NativeTool` is for. A new family is
warranted only when multiple formats share a tool concept with distinct
rendering semantics (that's how `SHADER` earned its slot).

## `extra` dict namespacing

`Document`, `Page`, `Stroke`, and `TextBlock` all have an
`extra: dict[str, Any]` for format-specific carry-through. Convention:
**top-level keys are format ids** (`"remarkable"`, `"goodnotes"`,
`"supernote"`, …) so payloads never collide. The key `"inkterop"` is
reserved for our own pipeline (currently: `extra["inkterop"]["point_rgb"]`,
per-point colors emitted by the reMarkable reader's `pen_style="rmc"`
mode and consumed by `render/primitives.py`). `restyled()` drops the
`"inkterop"` key; format keys survive restyling.

## Appearance semantics

`StrokeAppearance` describes how the SOURCE app renders the stroke;
`None` means "no observed styling — restyle from the tool family".

- `mode`: `stroked_constant` (one polyline, one width — `width` field
  set), `stroked_variable` (polyline + per-point WIDTH channel — `width`
  is `None`), `filled_outline` (variable width drawn as a filled
  polygon; the SVG backend tessellates this way, PDF currently
  approximates with piecewise-constant runs).
- `color`: the *resolved render* color — may differ from the stroke's
  semantic `color` (e.g. reMarkable's rendered eraser is white).
- `opacity`: stroke-level; a per-point `ALPHA` channel **wins** when
  present.
- `blend`: `normal | darken | multiply`. PDF and SVG cannot (yet) emit
  real blend modes; see `underlay`.
- `underlay: bool`: draw this stroke **beneath ordinary ink** — the
  approximation both backends use for reMarkable's `/BM /Darken`
  highlighter blend. Renderers make two passes: underlay strokes first
  (across all layers, in order), then everything else.
- `cap`/`join`: `round | square | butt`.

## Backgrounds

`Page.background` is one of four types (or `None`):

- `TemplateBackground(kind, name, pitch, line_width, dot_radius, gray)` —
  procedural template, `kind ∈ {dots, lines, grid, unknown}`, resolved to
  concrete params by the reader (see `formats/remarkable/templates.py`).
- `PdfBackground(attachment_key, page_index)` — a page of an attached
  PDF (key into `Document.attachments`). No renderer consumes this yet
  (the planned pikepdf base-page merge).
- `ImageBackground(image: RasterImage)` — bitmap background (Supernote
  BGLAYER).
- `ColorBackground(color)` — solid fill.

## Serialization (IR-JSON)

`ir/serialize.py` round-trips the whole model through JSON. Top-level
marker: `"inkterop_ir": 1` (bump `FORMAT_VERSION` on breaking change;
`document_from_dict` rejects other versions). Used by golden dumps,
`inkterop inspect --json`, and the `.json` reader/writer
(`formats/irjson.py`, detection = `"inkterop_ir"` in the first 4 KB).

- Lossless for everything except `Path` attachments, which serialize as
  paths unless `embed_attachments=True` inlines them base64 (the
  `IrJsonWriter` always embeds).
- Enums serialize as their string values; channels key by channel name;
  raster/image bytes are base64.

## Validation

`Document.validate()` (called by `convert()` before writing) walks every
stroke on every page:

- `len(x) == len(y)`;
- every channel list length equals the point count.

Readers should return documents that pass; writers may assume they do.
There is deliberately no range validation — `CHANNEL_RANGE` is a
declared contract, not a runtime check.

## Writing a new reader (checklist)

1. Create `formats/<name>/` (or a single module) exposing a class with
   `format_id`, `extensions`, `detect(path) -> bool` (cheap magic-byte
   sniff — the extension already matched, and ambiguous extensions like
   `.note` mean detect() must reject foreign files), and
   `read(path) -> ir.Document`.
2. Coordinates: convert to y-down, keep source units, set
   `Page.point_scale`. Document the unit derivation in
   `docs/formats/<name>.md` with confidence markers.
3. Channels: normalize to the units contract above. If the source stores
   device-rendered widths, emit `WIDTH` and do not synthesize widths from
   pressure. If the source stores raw channels and the app's rendering
   rule has been *measured* (`ir/renderrule.py`, fitted against the
   app's own export — see `docs/calibration-results.md`), bake the rule's
   output into per-point `WIDTH`/`ALPHA` so the IR holds the actual
   rendered look; writers apply the registry's inverse rule (the
   Excalidraw pattern: encode a target width as native params +
   synthetic per-point channel values).
4. Tools: map to `ToolFamily`; preserve the raw tool record in
   `NativeTool`.
5. Appearance: populate from *observed* source-app rendering (validated
   against the app's own export — see `docs/reverse-engineering.md`),
   or leave `None`. Constant opacity goes in `appearance.opacity`;
   per-point opacity in the `ALPHA` channel.
6. Anything decoded-but-unmapped goes in `extra[format_id]`, not dropped.
7. Register in `formats/__init__.py:_load()`.
8. Tests: fixtures must be self-generated or synthetic (see
   `docs/corpus-protocol.md`); include a `detect()` cross-format
   rejection test and a `Document.validate()` pass.
9. Writers additionally need the `validated` flag and the checklist in
   `docs/validated-writes.md`.
