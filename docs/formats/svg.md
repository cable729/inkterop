# SVG (generic ink reader) + Stylus Labs Write (.svg / .svgz)

Status: **read support**, two layers sharing one code base
(`core/src/inkterop/formats/svg/`):

- `SvgReader` (`format_id "svg"`) — a bounded generic subset of SVG 1.1,
  tuned so that our own SVG *writer* (`render/svg.py`) round-trips.
- `WriteReader` (`format_id "write"`) — Stylus Labs Write documents,
  whose native format is plain SVG (`.svgz` = the same file gzipped).

Registration order matters: both claim `.svg`, and `reader_for` returns
the first `detect()` hit, so `WriteReader` must be registered before
`SvgReader`. (The `.svg` *writer* slot belongs to `render/svg.py`'s
`SvgWriter`, unrelated to these readers.)

## Generic reader scope

Supported `[verified against hand-made fixtures]`:

| Feature | Notes |
|---|---|
| `<path>` | commands `M/m L/l H/h V/v C/c Q/q Z/z`; curves flattened with a fixed 16-segment subdivision |
| `<polyline>`, `<line>` | |
| styling | presentation attributes + inline `style=`: `stroke`, `stroke-width`, `stroke-opacity`, `fill`, `fill-opacity`, `opacity`, `stroke-linecap`, `color` (for `currentColor`) |
| `transform` | `translate` / `scale` / `matrix` / `rotate` flattened through a stack; `skewX/skewY` log-skipped |
| nested `<svg x y>` | treated as `translate(x, y)` `[inferred]` |
| `<text>` | -> `ir.TextBlock` |

Ignored by design: `<defs>`, `<clipPath>`, CSS `<style>` blocks, `<use>`,
gradients/patterns/masks/filters, `<image>`; paths containing `A`(rc) or
any other unsupported command are skipped whole with a logged warning.
Shapes with fill but **no stroke and no `data-rmi-*` attributes** are
skipped — in the wild they are decorations, not ink `[inferred]`.

### Units `[inferred]`

Page bounds come from the root `viewBox` (fallbacks: `width`/`height`,
then content extent). `point_scale` = (root width converted to pt) /
(viewBox width), with unit conversion pt=1, px/unitless=0.75 (CSS
96 dpi), in=72, mm=72/25.4, cm=72/2.54, pc=12.

### Round-tripping our own SvgWriter output `[verified by test]`

`render/svg.py` embeds per-stroke `data-rmi-tool`, `data-rmi-pressure`,
`data-rmi-width` attributes (space-separated per-point values) plus
`<g data-rmi-layer>` / `<g data-rmi-template>` groups. The reader:

- restores the tool family from `data-rmi-tool` and PRESSURE from
  `data-rmi-pressure`;
- STROKED_CONSTANT strokes are plain stroked paths; the WIDTH channel is
  `data-rmi-width` re-scaled so its first value equals the rendered
  `stroke-width` (the writer's unit scale is not stored) `[inferred]`;
- STROKED_VARIABLE strokes were tessellated into a closed filled outline
  (n forward points, optional 7-point round cap fan, n reversed points,
  optional fan; n = data-rmi channel length): the centerline is rebuilt
  as forward/reverse midpoints, per-point WIDTH as forward/reverse
  distances — geometry verified against `.rm` fixtures within 0.05 pt;
- all-coincident strokes became `<circle>`: center replicated to the
  channel length;
- `data-rmi-layer` groups -> `ir.Layer`; `data-rmi-template` content is
  skipped, only the background *kind* survives (pitch isn't embedded).

Per-point ALPHA and TIMESTAMP/tilt channels do **not** survive an SVG
round-trip (the writer never embeds them).

## Stylus Labs Write

License bright line: the Write app source (github.com/styluslabs/Write)
is AGPL and was **not** consulted. Facts below come from the
styluslabs/templates README (which documents the page structure), and
from the bytes of Write-produced documents: styluslabs.com serves its
website pages as Write SVGs (`corpus/third-party/styluslabs-write/`:
`site1_page002.svg`, `features_page002.svg`, plus template
`Dot grid 25.svg`; provenance in `corpus/third-party/MANIFEST.toml`).

### Structure

| Fact | Confidence |
|---|---|
| Multi-page: root `<svg id="write-document">` holding one `<svg class="write-page">` per page | `[verified]` in templates README + template files; site samples are single pages whose root is the page itself |
| `<g class="write-content write-v3">` carries page setup: `width`, `height`, `xruling`, `yruling`, `marginLeft`, `papercolor`, `rulecolor` | `[verified]` (README + samples; `width`/`height` absent on site samples -> fall back to the page `<svg>`'s width/height) |
| `rulecolor` is alpha-first `#AARRGGBB` (`#7F0000FF` matches the ruleline's `stroke-opacity="0.498"`); `papercolor` is `#RRGGBB` | `[verified]` |
| Ruling is drawn as real elements in `<g class="ruleline">` (with `rect.pagerect` page background) — decoration, skipped as content | `[verified]` |
| Pen strokes: `<path class="write-stroke-pen" fill="none" stroke="#RRGGBB" stroke-width="W" stroke-linecap="round" d="M x y l dx dy ...">` (absolute moveto + relative linetos) | `[verified]` |
| Handwritten hyperlinks: ink wrapped in `<a class="hyperref">`, and those paths carry **no** `write-stroke-*` class (site1: 149 classed + 27 unclassed strokes) | `[verified]` |
| `__comx` / `__comy` per-stroke attrs = center-of-mass bookkeeping, ignorable | `[inferred]` |
| Stroke classes other than `write-stroke-pen` (highlighter etc.) | `[unknown]` — unobserved; mapped to `ToolFamily.UNKNOWN` |
| Units = CSS px at 96 dpi -> `point_scale` 0.75 | `[inferred]` |
| Per-point width/pressure storage | `[unknown]` — observed strokes are constant-width; Write may bake dynamics into geometry |

### IR mapping

- `yruling>0 & xruling>0` -> `TemplateBackground("grid")`, `yruling`
  only -> `"lines"` (pitch = yruling), `xruling` only -> `"unknown"`;
  ruling gray derived from `rulecolor` luminance blended by its alpha.
- Non-white `papercolor` (no ruling) -> `ColorBackground`.
- `xruling`/`yruling`/`marginLeft`/`papercolor`/`rulecolor` raw strings
  are kept in `page.extra["write"]`.
- `write-stroke-<kind>` -> `NativeTool("write", kind)`; `pen` ->
  `ToolFamily.PEN`, everything else `UNKNOWN`.

Fixtures: `core/tests/fixtures/svg/` (hand-made, CC0 — `write-mini.svg`
is schema-by-us, not a Write export); real Write samples are
corpus-gated tests (`tests/test_svg_reader.py`).
