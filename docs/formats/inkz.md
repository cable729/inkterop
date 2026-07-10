# inkz (.inkz) — inkterop's notebook container

Status: **our own format** (reader + writer, `validated=True`), version 1.
Implemented in `core/src/inkterop/formats/inkz.py`; round-trip, dedup,
determinism and render-equivalence tests in `core/tests/test_inkz.py`.

`.inkz` is the interchange artifact of the "UIM as the stroke layer inside
a thin notebook container" decision: the bulk ink rides in **standard
Wacom UIM 3.1 files** (one per page, readable by any conformant UIM
consumer — see `docs/formats/uim.md` for the feature-fit matrix), and the
container supplies everything UIM has no home for: multi-page structure,
backgrounds, typed text, layer structure, attachments, and byte-faithful
native round-trip payloads.

## Container layout

A plain zip (deterministic: fixed 1980 timestamps, sorted member names,
deflate):

```
notebook.inkz
├── manifest.json            document + page structure (authoritative)
├── pages/0001.uim           stroke layer, standard UIM 3.1, one per page
│                            (omitted for pages with no strokes)
├── pages/0001.overlay.json  per-stroke data UIM cannot carry (optional)
└── blobs/<sha256>           content-addressed store: attachment PDFs,
                             background/layer images
```

Detection: zip containing `manifest.json` whose first 4 KB contain
`"inkterop_inkz"`.

## manifest.json

```jsonc
{
  "inkterop_inkz": 1,             // format version (reader rejects others)
  "format_id": "remarkable",      // source format provenance
  "title": "…", "orientation": "portrait",
  "metadata": { … }, "extra": { … },          // IR document carry-through
  "attachments": {"doc.pdf": {"blob": "<sha256>"}},
  "pages": [{
    "bounds": {"x_min": …, "y_min": …, "x_max": …, "y_max": …},
    "point_scale": 0.3171,        // source units -> PDF points
    "ink": "pages/0001.uim",      // null when the page has no strokes
    "background": …,              // oneof, see below
    "layers": [{                  // layer structure (strokes stored flat)
      "name": "ink", "visible": true,
      "n_strokes": 12,            // consecutive run of the page's strokes
      "texts": [{"x": …, "y": …, "text": …, "font_size": …, "color": …}],
      "raster": {"blob": "<sha256>", "format": "png", "bounds": …}
    }],
    "extra": { … }
  }]
}
```

- **Background oneof** (mirrors the IR `Background` union; same dict shape
  as IR-JSON): `{"type": "template", kind/name/pitch/line_width/
  dot_radius/gray}` | `{"type": "pdf", "attachment_key": …,
  "page_index": …}` | `{"type": "image", "blob": …, "format": …,
  "bounds": …}` | `{"type": "color", "color": …}` | `null`.
- **Blob store is content-addressed** — 200 pages sharing one imported
  PDF or image store it exactly once; attachments keep their IR keys.
- **Layers**: strokes are stored flat per page (all layers in layer
  order, invisible layers included); `n_strokes` runs reconstruct the
  layer structure on read. The manifest's bounds/point_scale are
  authoritative over anything derivable from the UIM part.

## pages/NNNN.uim — coordinates

Standard UIM 3.1 (see `docs/formats/uim.md`). Spline units are DIPs per
the UIM convention: `dip = source_units × point_scale / 0.75`; the reader
inverts with the same single factor applied to x, y, the WIDTH channel
and constant appearance widths.

## pages/NNNN.overlay.json — the fitness ledger

A JSON array index-aligned with the page's UIM strokes. Each entry holds
only what the UIM part could not: `appearance` (blend/underlay/cap/join/
geometry-mode/semantic-vs-render color), `tool_native`
(`NativeTool` — format id, tool id, params for byte-faithful same-format
round-trips), `extra` (namespaced format payloads). **The size of this
file is the running measure of how well UIM fits each source format** —
strokes that need no entry serialize as `{}`; a page whose overlay is all
`{}` omits the file entirely.

## Lossiness (deliberate and bounded)

Round-trip through `.inkz` preserves the IR except:

- geometry/WIDTH quantized to float32 (≤ 1e-4 source units at page
  scale), per-point ALPHA and colors to 1/255, sensor channels to the
  declared UIM precision (pressure ≤ 5e-5, timestamps ≤ 0.5 ms);
- the raw SPEED channel is dropped (recomputable from X/Y + TIMESTAMP).

Covered by `test_inkz.py::test_pinned_render_via_container`: the pinned
renderer's output for a document and for its `.inkz` round-trip must
pixel-match (strict mode, ≥ 99.9% ink-match).

## Changelog

- 2026-07-09: version 1 — manifest + per-page UIM 3.1 ink parts +
  content-addressed blobs + per-stroke overlay.
