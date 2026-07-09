# Xournal++ (.xopp) format

Status: **read + write, both fidelity-aware**; open, fully-documented format
(no reverse engineering needed — this is not a guessed spec). Round-tripped
by `core/tests/test_xopp.py`, including against a hand-shaped file meant to
resemble real Xournal++ output.

Confidence markers as in `docs/formats/goodnotes.md`, used sparingly here
since the container format itself is public; they mark claims about *our*
mapping choices and about real-world Xournal++ output we haven't sampled
directly.

## Container `[verified]`

Gzip-compressed XML, root `<xournal fileversion="4">` (the Xournal++
successor to the original Xournal `.xoj`; our reader/writer target
fileversion 4 only). `XoppReader.detect()` accepts either a gzip member
(magic `1f 8b`) or plain XML and checks for `<xournal` in the first 512
decompressed bytes — so ungzipped `.xopp`/`.xoj` files parse too.
`extensions = (".xopp", ".xoj")` on the reader; the writer only emits
`.xopp` (always gzipped).

Document tree: `<xournal><title/><page width height><background/>
<layer><stroke/><text/></layer></page>…</xournal>`. We read/write `title`,
`page`, `background` (partially), `layer`, `stroke`, `text`. We do not read
or write `<image>` elements (see Known gaps).

Implementation: `core/src/inkterop/formats/xopp/{common,reader,writer}.py`.
`common.py` holds the shared lookup tables both directions use.

## Coordinates & units `[verified]`

Xournal++ stores coordinates directly in PDF points, y down, and (in
practice) already rebased so the page's top-left is `(0, 0)` — there is no
separate scale or origin field in the format.

- **Read**: `Page.point_scale = 1.0` always; `Page.bounds = Rect(0, 0, w, h)`
  from the `<page>` element's `width`/`height` attributes (default
  612×792 if absent). `Document.orientation` is inferred as `"landscape"`
  when the first page is wider than tall, else `"portrait"`.
- **Write**: coordinates are rebased and scaled per the IR contract —
  `(x - bounds.x_min) * point_scale`, `(y - bounds.y_min) * point_scale` —
  so a foreign-unit page (e.g. reMarkable's x-centered canvas,
  `point_scale ≈ 0.3171`) lands correctly in point space starting at
  `(0, 0)`. Widths are scaled by the same `point_scale` factor. Verified by
  `test_scaled_coordinates` (reMarkable-shaped bounds/scale in, checks
  output page width/height and rebased stroke endpoints) and
  `test_remarkable_fixture_to_xopp` (real `.rm` fixture through the full
  reader → xopp → reader path, geometry checked to `abs=1e-4`).

## The `width` attribute `[verified]`

A `<stroke>`'s `width` attribute is a space-separated list of floats read
by point count `n`:

- **1 value** → constant-width stroke. IR: `appearance.mode =
  STROKED_CONSTANT`, `appearance.width` = that value, and the `WIDTH`
  channel is filled with the value repeated `n` times (so `Stroke.validate()`
  still sees one width per point).
- **≥ n values** (Xournal++'s own convention: **nominal width + one width
  per SEGMENT**, i.e. `n - 1` segment widths for `n` points, totaling `n`
  values) → variable-width stroke. IR: `appearance.mode =
  STROKED_VARIABLE`, `appearance.width = None`, and the `WIDTH` channel is
  built as `[nominal] + segment_widths[0:n-1]` — point 0 gets the nominal
  width, and point `i` (for `i ≥ 1`) gets the width of the segment ending
  at it. There is no dedicated "point 0" width in Xournal++'s own model;
  reusing the nominal value for point 0 is our choice, not a format fact.

On write, the inverse holds exactly: `_stroke_xml` emits the `WIDTH`
channel values verbatim as the `width` attribute (`n` values: index 0 is
treated as "nominal", the rest as segment widths) when `len(WIDTH) > 1`,
or a single value when the appearance is `STROKED_CONSTANT` or no `WIDTH`
channel exists (defaulting to `2.0`). Because read and write use the exact
inverse transform, `test_round_trip` checks the 3-point variable-width
pen stroke (`[1.5, 2.25, 3.0]`) survives byte-for-value round-trip via
`pytest.approx`.

## Tool mapping `[verified]`

Xournal++ only has three `tool` values: `pen`, `highlighter`, `eraser`.

- **Read** (`TOOL_TO_FAMILY`): `pen → PEN`, `highlighter → HIGHLIGHTER`,
  `eraser → ERASER`; anything else defaults to `PEN`.
- **Write** (`family_to_tool`, a collapse): `HIGHLIGHTER` and `SHADER` →
  `"highlighter"`; `ERASER` → `"eraser"`; every other family (`PEN`,
  `BALLPOINT`, `FINELINER`, `PENCIL`, `MECHANICAL_PENCIL`, `MARKER`,
  `BRUSH`, `CALLIGRAPHY`, `UNKNOWN`) → `"pen"`.

This is a **lossy** collapse in the pen-family direction: converting a
reMarkable ballpoint or fineliner stroke to xopp and back yields
`ToolFamily.PEN`, not the original sub-family, because Xournal++ has no
concept of pen sub-types to write it into. `NativeTool(FORMAT_ID, tool,
{})` is preserved on strokes we *read* from xopp, so a same-format xopp →
xopp round-trip of a file we didn't write ourselves still keeps its literal
`tool` string; strokes originating from other formats simply don't have a
native xopp tool record to fall back on.

## Color, alpha, opacity `[verified]`

- `color_to_hex(color, opacity)` writes `#RRGGBBAA`, 2 hex digits per
  channel, where the alpha byte is `color.a * opacity` — i.e. a stroke's
  per-point-independent opacity (`appearance.opacity`, defaulting to `0.5`
  for highlighters and `1.0` otherwise when there's no appearance to draw
  from) is folded into the single alpha byte on write. There is no
  separate opacity attribute.
- `hex_to_color` accepts both 6-digit (`RRGGBB`, alpha assumed `ff`) and
  8-digit (`RRGGBBAA`) hex strings. On **read**, the alpha byte is split
  back out: `opacity = color.a`, and the `Stroke.color` / `appearance.color`
  carry the RGB with `a` reset to `Color`'s default of `1.0`.
- `parse_color` also accepts Xournal++'s **named colors** instead of hex —
  `NAMED_COLORS` covers `black, blue, red, green, gray, lightblue,
  lightgreen, magenta, orange, yellow, white` (Xournal++'s preset palette
  `[inferred]`, not independently confirmed against Xournal++ source);
  an unrecognized name falls back to black. This is **read-only**
  compatibility — our writer always emits hex, never a named color.
  Verified against a hand-written fixture using `color="blue"` in
  `test_reads_handwritten_xournalpp_file`.

## Background style `[verified]`

`STYLE_TO_KIND` (read) / `KIND_TO_STYLE` (write) map between Xournal++'s
`<background style="…">` values and `TemplateBackground.kind`:

| xopp style | IR kind |
|---|---|
| `dotted` | `dots` |
| `lined` | `lines` |
| `ruled` | `lines` |
| `graph` | `grid` |
| `plain` | *(no background)* |

`lined` and `ruled` both read as `lines` (Xournal++ has used both terms
across versions `[inferred]`); on write we only ever emit `ruled` for
`lines` — round-tripping a `lined`-style file through our writer will
silently normalize it to `ruled`. A `style="plain"` (or missing
`<background>`) produces `Page.background = None`, not a `ColorBackground`.

Two things the reader/writer do **not** handle: a `<background
type="pdf">` (image/PDF page background) only triggers a warning log
(`_logger.warning("unsupported xopp background type %r", …)`) and the
page's `background` stays `None`; and on write, `ColorBackground` /
`ImageBackground` / `PdfBackground` pages are not special-cased at all —
the writer always emits `type="solid" color="#ffffffff"` and only inspects
`isinstance(page.background, TemplateBackground)` for the `style`
attribute. This matches the general IR note that no renderer/writer
consumes `PdfBackground` yet (`docs/ir.md`).

## Round-trip guarantees

Covered by `core/tests/test_xopp.py`:

- `test_round_trip` — full document (title, page bounds, `TemplateBackground`
  kind, two strokes of different tools/appearance modes, one text block with
  XML special characters) written and read back: geometry, `WIDTH` channel,
  color (to ~1/255 hex-quantization tolerance), highlighter
  `STROKED_CONSTANT` width/opacity/`underlay`, and text content all survive.
- `test_scaled_coordinates` — a page in foreign units (reMarkable canvas
  shape, non-zero-origin bounds, `point_scale = 685/2160`) rebases and
  scales correctly to point space on write, confirmed by re-reading.
- `test_reads_handwritten_xournalpp_file` — a file shaped like real
  Xournal++ output (not written by us): named color, single constant-width
  value, `graph` background style, 2-digit-precision alpha — parses
  correctly, i.e. we're compatible with the format as external tools emit
  it, not just self-consistent.
- `test_remarkable_fixture_to_xopp` — end-to-end: a real `.rm` fixture read
  by the reMarkable reader, converted to xopp, and read back; geometry
  checked to `abs=1e-4` after the unit conversion, `WIDTH` channel checked
  non-NaN.
- `test_raw_fidelity_rejected` — `Fidelity.RAW` raises `ValueError` (xopp
  has no field for raw pen dynamics; use IR-JSON or InkML instead), per the
  writer's docstring.

The writer sets `validated = True` — per `docs/validated-writes.md`, open
formats with round-trip test coverage are validated without a manual
app-open check; Xournal++ itself is also expected to open our output since
its own XML parser is documented as lenient (unverified independently — we
have not opened our output in Xournal++).

## Known gaps

- **Images** (`<image>` elements) are not read or written — `RasterImage`
  content in the IR has no xopp path.
- **PDF-background pages** (`<background type="pdf">`) are not read (logged
  and dropped) and `PdfBackground` is not written.
- **Audio** — Xournal++ has no audio-annotation concept in this format, so
  there is nothing to map; not a gap so much as N/A `[inferred]`.
- **Tool sub-family** is lossy through xopp in both directions except for
  `highlighter`/`eraser`, as described above.
- **Text formatting** — `<text>` only carries plain content, font, size,
  color, and position; no rich formatting, no font family beyond `"Sans"`
  written on our side.

## Changelog

- 2026-07-09: initial spec covering the reader/writer as implemented and
  tested.
