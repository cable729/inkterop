# PKDrawing fixtures

Apple PencilKit `PKDrawing.dataRepresentation()` blobs for
`tests/test_pkdrawing.py`, saved with our chosen `.pkdrawing` extension
(bare blobs have none — Apple Notes/Freeform embed them in databases).
Each blob ships with a `*.truth.json` ground-truth dump produced by the
same generator run that wrote the blob, by reading the in-memory
`PKDrawing` back through the public PencilKit API (stroke inks, colors,
renderBounds, and every control point's x/y/t/size/force/azimuth/
altitude/opacity/secondaryScale). PencilKit quantizes channel values at
`PKStrokePoint` construction, so the truth values are exactly what a
correct decoder must reproduce.

- `case00-empty` — empty drawing (container skeleton only).
- `case01-dot` — single 1-point pen stroke; every channel except
  location lands in the constant block (per-point mask 0x001).
- `case04-pressure-ramp` — 9-point pen stroke with a 0.1..1.0 force
  ramp (per-point force channel).
- `case06-inks` — three strokes with three deduplicated ink-table
  entries: black pen, black pencil, yellow marker (ink-index refs).

## Provenance

Self-made, no third-party ink data: generated 2026-07-09 on macOS 26
(Darwin 25.5) by our own Swift generator/oracle,
`corpus/scratch/pkgen.swift`, which builds strokes programmatically and
serializes them with Apple's PencilKit framework itself — so the blobs
are conformant by construction and the reader is tested against Apple's
writer rather than against itself. Build caveat: the binary must embed a
`CFBundleIdentifier` (`swiftc ... -Xlinker -sectcreate -Xlinker __TEXT
-Xlinker __info_plist -Xlinker pkgen-info.plist`) or PencilKit's
PKReplicaManager crashes in CFPreferences. Format spec:
`docs/formats/pencilkit.md`.

Fixture files are CC0-1.0.
