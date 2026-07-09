# Supernote `.note` (Ratta)

Reader: `core/src/rminterop/formats/supernote/` (`SupernoteReader`,
raster-first). Backed by [supernotelib](https://github.com/jya-dev/supernote-tool)
(Apache-2.0), pinned `>=0.7.1` in `core/pyproject.toml`; behavior below
was verified against supernotelib **0.7.1** source and synthetic
fixtures (`core/tests/fixtures/supernote/`). No real device files were
available during development — everything not exercised by the fixtures
is marked accordingly.

Confidence markers: [verified] = exercised by tests against supernotelib
0.7.1; [inferred] = read from supernotelib source but not exercised;
[unknown] = no evidence either way.

## Container overview

- [verified] X-series files: 4-byte file type (`note`; `mark` for
  annotation files) followed by an ASCII signature `SN_FILE_VER_YYYYNNNN`
  at offset 4. supernotelib 0.7.1 knows signatures `20200001` (fw C.053)
  through `20260016` (fw Chauvet 3.28.42).
- [inferred] Original pre-X devices: signature `SN_FILE_ASA_20190529` at
  offset 0, no layers, zlib (`SN_ASA_COMPRESS`) page bitmaps.
- [verified] Body is a sequence of length-prefixed blocks (4-byte LE
  length + payload). Metadata blocks are `<KEY:VALUE>` strings; the last
  4 bytes of the file hold the footer block's address; the footer maps
  `PAGE<n>`, `FILE_FEATURE` (header), covers, titles, keywords, links to
  block addresses.
- [verified] Each page block references up to 5 layer metadata blocks
  (`MAINLAYER`, `LAYER1..3`, `BGLAYER`), each pointing at an encoded
  bitmap (`LAYERBITMAP`). Ink bitmaps use the `RATTA_RLE` protocol
  ((colorcode, length) byte pairs, grayscale color codes); `BGLAYER`
  templates may also be PNG (`user_*` page styles, [inferred]).
- [verified] Page metadata carries `LAYERSEQ` (z-order, top first),
  `LAYERINFO` (visibility JSON with `:` stored as `#`), `ORIENTATION`
  (`1000` portrait / `1090` horizontal), `PAGESTYLE`, and a `TOTALPATH`
  block address.

## Vector vs raster reality

**Raster-first.** supernotelib stores the `TOTALPATH` block — which is
where the device keeps per-stroke path data — as opaque bytes
(`Page.get_totalpath()`), and no code in 0.7.1 decodes it [verified:
grep of the installed package; `manipulator.py` copies it verbatim].
The library's "vectorize" option in `SvgConverter`/`PdfConverter` is
potrace bitmap tracing of the *rendered page image*, not pen strokes
[verified: `converter.py` imports `potrace` and traces
`ImageConverter` output]. So real per-point vector data (coordinates,
pressure, pen type per stroke) is **not practically accessible** through
supernotelib, and this reader emits no `ir.Stroke`s.

What the reader produces instead:

- One `ir.Layer` per Supernote ink layer that has bitmap content, bottom
  to top (reversed `LAYERSEQ`), each holding a full-page RGBA PNG
  (`ir.RasterImage`) rendered by supernotelib's `ImageConverter` with a
  visibility overlay isolating that layer and hiding the background —
  which makes untouched pixels transparent [verified].
- Layer visibility from `LAYERINFO` [verified for visible layers;
  hidden-layer files not exercised].
- `BGLAYER` content (template/custom background), when present, becomes
  an `ir.ImageBackground` [inferred — fixtures have no BGLAYER bitmap].
- Pre-X non-layered pages: one flattened raster layer named `page`
  [inferred — no legacy fixture].
- `page.extra["supernote"]`: page id, style, orientation, and the size
  of the undecoded `TOTALPATH` block (kept as a breadcrumb for a future
  true vector decoder).

## Geometry

- [verified] Portrait canvas 1404x1872 px (A5X, A6X2 and kin);
  `APPLY_EQUIPMENT:N5` devices (A5X2/Manta) use 1920x2560
  [inferred — constant from `fileformat.py`, no fixture].
- [verified] Horizontal pages (`ORIENTATION:1090`) decode with swapped
  dimensions (1872x1404); the reader swaps page bounds to match and sets
  document orientation from page 0.
- Coordinates: page units = device pixels, origin top-left, y down.
  `point_scale = 595.0 / device_width` (595 pt ≈ full portrait width),
  so an A5X page lands at ~595x793 pt — close to letter/A4. The scale is
  the same constant for landscape pages (same pixel pitch).

## Detection

`detect()` accepts `note`/`mark` + `SN_FILE_VER_` at offset 4, or
`SN_FILE_ASA_` at offset 0. Notability's `.note` files are PK zip
archives and are rejected [verified by test], as are reMarkable `.rm`
files.

## Device / firmware coverage

- [verified] Synthetic X-series files, signature `SN_FILE_VER_20220011`
  (Chauvet 2.5.17 era), RATTA_RLE, layered pages.
- [inferred] Later signatures through `20260016` parse identically;
  `>= 20230015` files switch to `RattaRleX2Decoder` color codes inside
  supernotelib (handled transparently by `ImageConverter`).
- [unknown] Real device output quirks (multi-ink-layer notes, hidden
  layers, custom `user_*` templates, links/titles/keywords, `.mark`
  companions, pre-X `SN_ASA_COMPRESS` files) — code paths exist but are
  untested against real captures.

## Renderer gap

`render/pdf.py` does not yet draw `Layer.raster` (it also skips pages
with no drawable strokes), so Supernote documents currently render to
blank PDF pages. The smoke test asserts document structure and that the
PDF pipeline doesn't reject the document; raster drawing support in the
renderer is the follow-up needed for visible output.

## Attribution

Container reverse-engineering and all decoding come from
`supernote-tool`/`supernotelib` by jya (github.com/jya-dev/supernote-tool),
Apache License 2.0. The test fixtures are synthetic (generated by
`core/tests/fixtures/supernote/make_fixture.py`) and contain no
third-party data.
