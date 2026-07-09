# InkML mapping (rminterop)

How `rminterop.formats.inkml` maps the IR onto W3C InkML
(https://www.w3.org/TR/InkML/), and where it extends the standard.
InkML is the raw-fidelity flagship: the one output format that carries
every per-point channel *and* the exact source appearance in a single
standard-shaped file.

Confidence markers: **[verified]** = covered by round-trip tests in
`core/tests/test_inkml.py`; **[spec]** = per the W3C spec, exercised
only by hand-written fixtures here.

## Document structure

```xml
<ink xmlns="http://www.w3.org/2003/InkML">
  <annotation type="title">…</annotation>            <!-- optional -->
  <definitions>
    <context xml:id="ctx0"><traceFormat>…</traceFormat></context>
    <brush xml:id="br0">…</brush>
  </definitions>
  <traceGroup xml:id="page0">                        <!-- one per page -->
    <annotationXML type="rminterop-page"><page …/></annotationXML>
    <traceGroup>                                     <!-- one per layer -->
      <annotationXML type="rminterop-layer"><layer …/></annotationXML>
      <trace contextRef="#ctx0" brushRef="#br0">x y f …, x y f …</trace>
    </traceGroup>
  </traceGroup>
</ink>
```

One `<context>` is emitted per distinct channel *set*; one `<brush>`
per distinct (tool, semantic color, appearance) combination — the brush
body is the dedup key. **[verified]**

## Channel table

Channel order within a context is always X, Y, then the rows below in
this order (only those present on the stroke):

| IR channel      | InkML name | units | declared attrs        | transform (write)              | standard? |
|-----------------|------------|-------|-----------------------|--------------------------------|-----------|
| x (implicit)    | `X`        | pt    | `type="decimal"`      | `(x − bounds.x_min) · point_scale` | yes   |
| y (implicit)    | `Y`        | pt    | `type="decimal"`      | `(y − bounds.y_min) · point_scale` | yes   |
| `PRESSURE`      | `F`        | 0–1   | `min="0" max="1"`     | as-is                          | yes (force) |
| `TILT_AZIMUTH`  | `OA`       | rad   | `units="rad"`         | as-is                          | yes       |
| `TILT_ALTITUDE` | `OE`       | rad   | `units="rad"`         | as-is                          | yes (elevation) |
| `TIMESTAMP`     | `T`        | s     | `units="s"`           | as-is (seconds since stroke start) | yes   |
| `WIDTH`         | `W`        | pt    | `units="pt"`          | `width · point_scale`          | name reserved by spec for stroke width; our pt values are an rminterop convention |
| `SPEED`         | `S`        | —     | —                     | as-is (source units/s)         | **no** — spec reserves `S` for tip-switch state; rminterop reuses it |
| `ALPHA`         | `A`        | 0–1   | `min="0" max="1"`     | as-is                          | **no** — rminterop extension |

The reader inverts every transform (`x = X / point_scale + x_min`,
`WIDTH = W / point_scale`) using the page annotation; foreign files
without it get `point_scale = 1.0` and no rebase. **[verified]**

Trace values are rounded to 4 decimals (≤ 5·10⁻⁵ pt geometric error);
points are comma-separated, channel values space-separated, e.g.
`<trace>10.5 20 0.55, 11 21 0.6</trace>`. **[verified]**

## Value prefixes (read side)

Per InkML §3.2 the reader decodes prefixed trace values — OneNote and
other producers emit these:

- `'v` — first difference (velocity): value = previous + v
- `"a` — second difference (acceleration): velocity += a, value += velocity
- `!v` — explicit value, resets the channel to explicit mode
- unprefixed — interpreted in the channel's *current* mode: a `'` or
  `"` prefix persists for following values of that channel until
  changed. **[spec, verified against hand-written traces]**

The writer always emits plain explicit values. **[verified]**

## Brush mapping

Standard `brushProperty` entries (advisory, for foreign consumers):

| property       | value                                             |
|----------------|---------------------------------------------------|
| `color`        | `#rrggbb` of the render color (appearance color, else semantic color) |
| `transparency` | `1 − opacity`                                     |
| `width`        | constant width in pt, only for `STROKED_CONSTANT` appearances (uses the point_scale of the page where the brush first occurs) |

Lossless state lives in the `annotationXML`; the hex/4-decimal
brushProperty values are derived and never read back when the
annotation is present.

## annotationXML schema (rminterop extension, non-standard)

All float attributes in annotations use `repr(float)` — exact
round-trip, unlike the 4-decimal trace values. **[verified]**

### Brush: `<annotationXML type="rminterop">`

```xml
<tool family="highlighter">                        <!-- ToolFamily value -->
  <native formatId="remarkable" toolId="5" toolIdKind="int|str"
          params='{"…": …}'/>                      <!-- NativeTool; params is JSON -->
</tool>
<color r="1.0" g="0.93" b="0.46" a="1.0"/>         <!-- semantic stroke color -->
<appearance mode="stroked_constant" width="30.0"   <!-- width: SOURCE units; absent => WIDTH channel -->
            opacity="0.85" blend="darken" cap="square" join="square"
            underlay="true">
  <renderColor r="…" g="…" b="…" a="…"/>
</appearance>
```

`<appearance>` is omitted when `Stroke.appearance is None` and the
reader restores `None` (target restyles from the tool family).
`<native>` is omitted when there is no NativeTool. Enum values are the
IR enum strings (`GeometryMode`/`BlendMode`/`LineCap`); unknown values
fall back to variable/normal/round on read. **[verified]**

### Page: `<annotationXML type="rminterop-page">`

```xml
<page xMin="-810.0" yMin="0.0" xMax="810.0" yMax="2160.0"
      pointScale="0.3171296296296296" orientation="portrait"/>
```

Bounds are in SOURCE units (rM: x centered on 0, grown y). Orientation
is the document-level hint, stamped on every page; the reader takes the
first page's value. **[verified]**

### Layer: `<annotationXML type="rminterop-layer">`

```xml
<layer name="ink" visible="true"/>
```

## Fidelity levels

- `EXACT` and `RAW` produce byte-identical output — InkML holds both
  the appearance and the raw dynamics at once. **[verified]**
- `NATIVE` applies `ir/defaults.py: restyled()` before writing, so
  brushes reflect semantic tool-family defaults instead of the source
  app's observed rendering. **[verified]**

## Reading foreign InkML

- Namespace-agnostic tag matching (default ns, prefixed, or none).
- `contextRef`/`brushRef` resolved on traces, inherited from enclosing
  `traceGroup`/`ink` when absent; no context ⇒ `X Y`. Contexts and
  standalone `traceFormat` elements are indexed by `xml:id` (or bare
  `id`), and channel order is honored per context. **[verified]**
- Traces directly under `<ink>` become a single implicit page/layer;
  page bounds fall back to trace extents with `point_scale = 1.0`
  (empty file ⇒ US Letter). **[verified]**
- Brushes without our annotation contribute only `brushProperty color`
  as the stroke color; no-brush traces get `ToolFamily.PEN`, black,
  `appearance = None`. **[verified]**
- Unknown channel names are ignored; short rows are zero-padded, extra
  values dropped.

## Not carried

TextBlocks, raster layers, page backgrounds/templates, and attachments
have no InkML representation and are dropped on write. `Stroke.extra`
and `Page.extra` are not serialized (use IR JSON for those).

## Known consumers

- **Microsoft OneNote / Graph API** accepts InkML page content and
  emits `'`/`"`-prefixed traces (the reason the prefix decoder exists).
  It ignores our annotationXML but reads X/Y/F traces.
- **Windows Ink / ISF tooling** and **InkscapeInkML-ish converters**
  generally handle X/Y(/F) plain traces; the W/S/A channels are
  declared in the context so conforming parsers can skip them.

## detect()

A file is InkML if the first 2048 bytes contain both `<ink` and
`InkML` (the namespace URI). **[verified]**
