# OneNote (.one) format

Status: **read support for ink** (+ best-effort typed text and page
titles), classic OneNote 2016 revision-store files only. No writer.
Verified 2026-07-09 against the onenote.rs sample corpus
(`corpus/third-party/onenote.rs/.../tests/samples`, MIT-licensed samples):
`joplin/scaled_ink.one`, `handwriting_recognition.one`,
`joplin/desktop_missing_ink.one`, `joplin/onenote_desktop.one` plus
non-ink sections; rendered output shows legible handwriting ("Hello
World", "This is a quick test", 2⁰…2⁵) in plausible page positions.

Sources (license-checked):

- **MS-ONESTORE / MS-ONE** — Microsoft Open Specification Promise. The
  container layer is fully documented there; everything marked
  `[verified]` below matches those specs and the samples.
- **"Decoding OneNote's File Format Secrets"** (m-siemens.de, May 2026)
  — prose description of the *undocumented* ink hierarchy (ink is
  explicitly absent from MS-ONE). Facts used, no code.
- **onenote.rs** (MPL-2.0) — structure facts only, no code ported.
  Sample corpus used for validation.
- **MS-ISF** — the multibyte number encodings OneNote reuses for ink;
  implementation shared with our ISF reader
  (`core/src/inkterop/formats/isf.py`).

Implementation: `core/src/inkterop/formats/onenote/`
(`onestore.py` = container, `reader.py` = ink extraction + IR).

## Two packagings, one extension

| guidFileFormat @ offset 48 | Meaning | Support |
|---|---|---|
| `{109ADD3F-911B-49F5-A5D0-1791EDC8AED8}` | classic OneNote 2016 revision store | **read** |
| `{638DE92F-A6D4-4BC1-9A36-B3FC2511A5B7}` | FSSHTTPB packaging (OneDrive/cloud-synced) | detected, rejected with a clear error — out of scope |

Both start with guidFileType `{7B5C52E4-D88C-4DA7-AEB1-5378D02996D3}`
(`.one`) or `{43FF2FA1-EFD9-4C76-9EE2-10EA5722765F}` (`.onetoc2`) at
offset 0 — that GUID is `detect()`'s magic `[verified]`. Cloud users
already have a lossless escape hatch: OneNote's Graph API serves ink as
InkML, which inkterop reads natively, so the FSSHTTPB gap mostly matters
for local legacy files.

## Container walk `[verified: MS-ONESTORE]`

1. **Header** (1024 B): `cTransactionsInLog` @96, `fcrTransactionLog`
   @160, `fcrFileNodeListRoot` @172 (FileChunkReference64x32 = u64 stp +
   u32 cb).
2. **Transaction log**: fragment chain of (srcID u32, switch u32)
   entries; srcID 1 is the commit sentinel. Entries give the CUMULATIVE
   valid-FileNode count per FileNodeList; only the first
   `cTransactionsInLog` transactions count. This bounds every list walk
   (uncommitted trailing nodes are real in the wild).
3. **FileNodeListFragment**: magic `0xA4567AB1F5F7F4C4` + listID + seq
   (16 B), FileNode stream, padding, next-fragment ref (12 B), footer
   `0x8BC215C38233BA4B` (8 B).
4. **FileNode** header u32: ID bits 0-9, size bits 10-22 (whole node),
   stpFormat 23-24, cbFormat 25-26 (compressed formats store value/8),
   baseType 27-30 (1 = ref to data, 2 = ref to child FileNodeList).
5. **Root list** → `ObjectSpaceManifestRootFND` (0x004, root gosid) +
   `ObjectSpaceManifestListReferenceFND` (0x008) per object space →
   manifest list, whose **last** `RevisionManifestListReferenceFND`
   (0x010) is current (§2.1.6) → revision manifest list.
6. **Revisions** (0x01B/0x01E/0x01F …0x01C): each contains
   `ObjectGroupListReferenceFND` (0x0B0) → group list: GlobalIdTable
   (0x021/0x022/0x024/0x028: index→GUID) + object declarations
   (0x0A4/0x0A5/0x0C4/0x0C5 modern; 0x02D/0x02E legacy; 0x072/0x073
   file-data) + `RootObjectReference2/3FND` (0x059/0x05A) naming the
   content root (role 1).
   We replay all revisions in list order into one object map — later
   declarations shadow earlier — and keep the *first* declaration per
   root role `[inferred]`: yields the newest state on every sample
   without materializing revision-dependency history.
7. **ObjectSpaceObjectPropSet** (§2.6.1): OID stream (header u32: count
   bits 0-23, extended-streams bit 30, OSID-absent bit 31; then
   CompactIds = n bits 0-7 + guidIndex bits 8-31), optional OSID /
   ContextID streams, then PropertySet: u16 count, PropertyIDs (id bits
   0-25, type 26-30, bool bit 31), values back-to-back. Property types:
   1 empty, 2 bool-in-id, 3/4/5/6 u8-u64, 7 four-byte-length data, 8/9
   ObjectID/array (data lives in the OID stream, consumed **in property
   order**, arrays store only their count), A-D OSID/context analogues,
   0x10 array-of-property-values, 0x11 nested property set.

## Ink hierarchy `[inferred: undocumented; validated on samples]`

Observed JCIDs (object type ids):

| JCID | Name | Carries |
|---|---|---|
| `0x0006000B` | jcidPageNode | PageWidth/Height 0x1C01/02 (f32, half-inch), CachedTitleStringFromPage 0x1D3C, ElementChildNodes 0x1C20 |
| `0x00060037` | jcidPageManifestNode | role-1 root of a page space; ContentChildNodes → PageNode |
| `0x00060014` | jcidInkContainer | OffsetFromParentHoriz/Vert 0x1C14/15 (f32, **half-inch**), InkScalingX/Y 0x1C46/47 (f32), InkData 0x3415 (ref), or ContentChildNodes 0x1C1F → nested ink groups |
| `0x0002003B` | jcidInkDataNode | InkStrokes 0x3416 (ref array), InkBoundingBox 0x3418 (4×i32) |
| `0x00020047` | jcidInkStrokeNode | InkPath 0x340B (bytes), InkStrokeProperties 0x3409 (ref) |
| `0x00120048` | jcidStrokePropertiesNode | InkDimensions 0x340A, InkColor 0x340F (COLORREF 0x00BBGGRR), InkWidth/Height 0x340D/0C (f32 HIMETRIC), InkPenTip 0x3412, InkRasterOperation 0x3413, InkTransparency 0x3414 |

Other page-tree JCIDs observed while walking: 0x0006000C outline,
0x0006000D outline element, 0x0006000E rich text, 0x0006002C title.

### InkPath decode `[verified on samples]`

ISF multibyte encoding (7-bit little-endian groups, continuation high
bit; signed = sign-flip `(abs<<1)|sign` — shared primitives imported
from `formats/isf.py`):

    signed-multibyte COUNT, then COUNT signed-multibyte DELTAS

Layout is dimension-major (all X, then all Y, then pressure …,
`len(values) / len(dimensions)` points). Values are plain first-order
**deltas** — `coord[i] = coord[i-1] + delta[i]` — confirmed empirically:
cumulative sums produce smooth, page-plausible strokes; raw values do
not. (No delta-delta, no Huffman, unlike ISF packet data.)

### InkDimensions entry (32 bytes) `[inferred]`

    GUID (16) + i32 lower + i32 upper + u32 unit + f32 resolution

Observed GUIDs are the ISF packet-property GUIDs:

| GUID | Dimension | Limits observed | unit/resolution |
|---|---|---|---|
| `{598A6A8F-52C0-4BA0-93AF-AF357411A561}` | X | ±2³¹ | 2 (cm), 1000.0 |
| `{B53F9F75-04E0-4498-A7EE-C30DBB5A9011}` | Y | ±2³¹ | 2 (cm), 1000.0 |
| `{7307502D-F9F4-4E18-B3F2-2CE1B1A3610C}` | pressure | [0, 32767] | — |

1000 per cm ⇒ native ink unit is **HIMETRIC** (0.01 mm), matching ISF.

### Geometry mapping `[inferred, plausibility-validated]`

    page_pt = Σ(ancestor offsets, half-inch) * 36
              + cumsum(deltas) * InkScaling * 72/2540

Validated on `scaled_ink.one`, where one container has InkScalingY=7.18
and OffsetFromParentVert=−19.03: both strokes land side by side inside
the page only under this formula. Page offsets in half-inch increments
are documented ([MS-ONE] 2.3.18/2.3.19); the HIMETRIC ink unit and the
InkScaling semantics are not.

## IR mapping

- One `ir.Page` per object space whose role-1 root is (or leads to) a
  PageNode; page units are PDF points (`point_scale = 1.0`); bounds =
  page size (half-inch × 36) grown to the ink extent.
- Strokes: x/y as above; pressure → `PRESSURE` channel normalized by the
  dimension's limit span; COLORREF → color; InkWidth (HIMETRIC) →
  constant-width appearance; InkRasterOperation 9 → highlighter
  (underlay + darken, same convention as ISF `[inferred]`); opacity =
  1 − transparency/255 `[inferred]`; pen tip 1 → square cap.
- Best-effort extras: page title (CachedTitleStringFromPage), rich-text
  runs (RichEditTextUnicode 0x1C22) as `TextBlock`s at their ancestors'
  accumulated offsets.

## Known limitations / open questions

- **FSSHTTPB packaging**: out of scope (see above). A future reader
  would go through MS-FSSHTTPB data elements + object groups.
- **Writer**: out of scope (validated-writes policy would apply; the
  official cloud API is the safe write path).
- Ink nested inside outline elements is positioned only from explicit
  ancestor offsets; OneNote's text-layout-derived line positions are
  not reproduced, so such ink can sit at the wrong y. Page-level ink
  (the normal drawing case) is exact.
- `InkBoundingBox` (unscaled ink units) is parsed but unused — stroke
  extents serve; its exact role vs. layout is `[unknown]`.
- Stroke-node properties 0x3419/0x341A/0x341B/0x341D/0x345B/0x3420 and
  the embedded-ink property family 0x349E-0x34A5 (inline handwriting
  metrics) are observed but undecoded `[unknown]`.
- InkTransparency polarity assumed = ISF (0 opaque) `[inferred]`; no
  sample exercises it.
- GlobalIdTableEntry2/3FNDX (`.onetoc2` id-table inheritance) are
  skipped; `.onetoc2` files carry no ink.
- Legacy object-revision nodes (0x041/0x042) replace an object's
  property set wholesale in our replay; partial-update semantics
  `[unknown]`, not observed in the corpus.

## Changelog

- 2026-07-09: initial reader — full classic ONESTORE walk (transaction
  log, fragment chains, object spaces, latest-revision replay, property
  sets), ink extraction with pressure, titles + plain text, corpus +
  synthetic tests, PDF smoke-validated renders.
