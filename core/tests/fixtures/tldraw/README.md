# tldraw fixture

`two-pages.tldr` — hand-authored to the documented schema (tldraw.dev
docs pages; NO tldraw source code was read — custom source-visible
license), CC0. Pending replacement/augmentation by an app-made sample
from tldraw.com.

Contents, across two pages:

- Page 1: a pen draw stroke (`isPen: true`, varying `z` pressures
  0.2–1.0, red, size m), a straight-segment draw stroke (black, size
  s), a highlight stroke (yellow, size l), a rich-text text shape
  ("hello tldraw", blue), and a `geo` rectangle (must be *skipped* —
  unmodeled shape type).
- Page 2: one draw stroke (light-blue, size xl).

Plus non-shape records the reader must ignore (document, camera).
Field facts are [inferred] until re-verified by loading this file at
tldraw.com (see docs/formats/tldraw.md open questions).
