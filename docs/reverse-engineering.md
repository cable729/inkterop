# Reverse-engineering methodology

How this repo decodes undocumented (or under-documented) ink-note formats,
and the legal/ethical boundaries it stays inside while doing it. This is
the guide for anyone extending `formats/` to a new app — read it before
opening a hex editor.

**Not legal advice.** The framing below reflects how this project is run;
it is not a substitute for your own legal judgment, especially outside the
US.

## Why this is legitimate

rminterop exists to make note files **interoperate** — read a format you
already have files in, convert them to formats other tools understand. It
only ever operates on files the user owns and already has access to
(exports, backups, the local app cache). No sample in this repo's history
has involved DRM, encryption, or any access-control mechanism: every
format inspected so far turned out to be plain container formats (zip,
gzip, protobuf, bplist) wrapping unencrypted geometry data — GoodNotes
files, for instance, are a plain ZIP with Apple-framed LZ4 inside, no
encryption layer at all. **No DRM circumvention has been encountered or
performed.**

For US-based readers: this kind of reverse engineering for the purpose of
achieving interoperability between independently created programs is the
scenario 17 U.S.C. §1201(f) carves out an exemption for. It applies to
circumvention of *access controls*, which so far has been moot here since
nothing inspected has had one — but it's the relevant doctrine if that
ever changes. Again: not legal advice, and not a substitute for reading
the statute and your own counsel if you're doing this commercially or at
scale.

## Sample ethics

- Third-party samples (real app output we didn't generate ourselves) live
  in `corpus/third-party/`, which is **gitignored** — nothing in it is
  committed or redistributed. Every addition gets a provenance entry in
  `corpus/third-party/MANIFEST.toml`: source URL, license, fetch date,
  method, and intended use.
- Only **self-generated** fixtures — files we created ourselves following
  `docs/corpus-protocol.md`, containing no personal data — are promoted
  into git, as `core/tests/fixtures/<format>/`. Those are CC0 (public
  domain): synthetic test strokes on blank templates, safe to redistribute
  and safe for anyone to build on.
- Third-party samples of unclear or personal provenance (e.g. the
  Notability sample under `corpus/third-party/notability-reader-sample/`,
  pulled from someone else's public repo) are study-only, never quoted
  into fixtures, never redistributed.

## The GPL boundary (exact policy)

This repo is MIT-licensed. Some of the best public documentation of these
formats lives in GPL-licensed projects. The line we hold:

- **Format facts are not copyrightable.** Byte layouts, field orders, magic
  numbers, wire-format shapes — these are facts about how a file is
  structured, not creative expression. They can be learned from any
  project's documentation (README, wiki, issue tracker) regardless of that
  project's license, and used to write an independent MIT-licensed decoder.
- **GPL source code must never be read into or ported into this repo.**
  Not copied, not "rewritten from memory" after reading, not architecturally
  mirrored. If a GPL project's code is the only source of a fact, we don't
  use that fact until we can establish it ourselves (see the workflow
  below) or it turns up somewhere unencumbered.

**Case study — GoodNotes.** The container layout, LZ4 framing, and the
basic stroke-triplet (x, y, width) shape were first published in the
README of [franzthiemann/goodparse](https://github.com/franzthiemann/goodparse)
(GPL-3.0). We used those documented *facts* and goodparse's public sample
files (`corpus/third-party/goodparse/samples/`) to write an independent
decoder (`core/src/rminterop/formats/goodnotes/wire.py`) —
goodparse's Python source was **deliberately not opened or read**. The
typed-section layout inside the geometry blob (the `tpl\0` structure
documented in `docs/formats/goodnotes.md`) goes beyond what goodparse's
README covers and is this repo's own finding, arrived at by the workflow
below. Findings that extend or correct goodparse's public documentation
get reported back as **issues**, never as code contributions containing
anything derived from reading their source.

## The workflow

Applied, in order, to every new format:

1. **Inventory.** Run `tools/re/inventory.py` on the container (a zip or a
   directory). It reports, per member: size, a magic-byte guess, and
   Shannon entropy. High entropy *without* a recognized magic number is
   the signal for "this is compressed or encrypted, decode before reading
   further" — it's what first flagged GoodNotes' geometry blobs as
   Apple-framed LZ4 rather than raw floats.
2. **Structure decode**, matched to what step 1 found:
   - Looks like protobuf (a plausible field-number/wire-type byte at the
     start)? `tools/re/pbwire.py` is a schema-less `protoc --decode_raw`
     equivalent — walks tag/wire-type framing without a `.proto` file.
   - `bplist00` magic? Python's stdlib `plistlib` reads it directly;
     NSKeyedArchiver-wrapped archives (Notability's `Session.plist`) need
     UID resolution on top, which `plistlib` also handles natively — no
     Foundation/PyObjC required.
   - `bv41`/`bv4-` framing? `tools/re/applelz4.py` is an independent
     decoder for Apple's `libcompression` framed LZ4 (frame format is
     public; the decoder is written from that spec, not from any
     existing implementation).
3. **Known-shape corpus experiments.** Once the container structure is
   legible, the meaning of individual fields is pinned down by writing
   controlled inputs and diffing the output (this is what
   `docs/corpus-protocol.md`'s numbered case matrix is *for*):
   - a straight horizontal line isolates axis mapping and units (one
     coordinate stays constant; its value plus the doc's declared page
     size gives you the scale factor);
   - a corner-to-corner diagonal isolates origin and orientation (which
     corner is `(0,0)`, does y grow up or down);
   - a pressure ramp (light → heavy) isolates which channel/column moves
     monotonically with applied pressure, and whether it's already
     device-rendered width or raw pressure needing a formula.
4. **Hypothesis iteration until every byte is accounted for.** A decode
   isn't done when it produces plausible-looking numbers — it's done when
   parsing the blob consumes it with **zero residual bytes** and every
   section's role is at least hypothesized. The GoodNotes `tpl\0`
   typed-section parse (`docs/formats/goodnotes.md`) is the reference
   example: the decompressed geometry blob is `"tpl\0" + u32 total_length
   + ASCII type-signature + one section per signature token`, and
   `parse_tpl()` is checked against **all public samples with no residual
   bytes** before any section's semantics were trusted.
5. **Rendering validation gate.** Before a decode is trusted for anything
   beyond "the bytes parse," render our interpretation and compare it
   against the *source app's own* PDF export of the same document —
   overlay-diff, not just eyeballing. Two data points on this so far:
   - the reMarkable v6 renderer was checked against the official desktop
     export of a real 15-page notebook and landed within **~2%** on page
     geometry (`docs/formats/remarkable.md`) — the strongest validation
     this repo has done, because it's a controlled, single-source
     comparison;
   - the GoodNotes geometry parse was cross-checked visually against
     `corpus/third-party/goodparse/samples/Test4.pdf` (the app's own PDF
     export sitting alongside `Test4.goodnotes` in the same sample set) —
     less rigorous than the reMarkable check (no scripted overlay-diff
     yet, no controlled corpus), which is exactly why
     `docs/formats/goodnotes.md` still marks GoodNotes "experimental" and
     `docs/corpus-protocol.md` exists: to get GoodNotes and Notability to
     the same controlled-corpus rigor reMarkable already has.

## Confidence vocabulary

Every format doc (`docs/formats/*.md`) tags claims with one of three
markers:

- **`[verified]`** — established either by a controlled experiment (a
  known-shape corpus case whose expected encoding was predicted, then
  confirmed) or by an invariant-checked decode plus the rendering
  validation gate above (zero-residual-byte parsing across all samples,
  checked against the app's own export).
- **`[inferred]`** — consistent with everything observed so far, but no
  isolating experiment has confirmed it in particular; a plausible reading
  that hasn't been stress-tested against a corpus case designed to break
  it.
- **`[unknown]`** — bytes are observed and their existence is documented,
  but no meaning has been established at all (not even a guess we'd stand
  behind).

A finding only gets to graduate from `[inferred]` to `[verified]` by
passing through step 3+4 above with a corpus case built specifically to
isolate it — not by staring at more samples of the same kind.

## Version-drift discipline

Formats change under active apps. Every corpus file records the app
version and OS version it was produced on
(`corpus/manifest.toml` per `docs/corpus-protocol.md`); every format doc
carries a "verified against version X" statement (see the status lines at
the top of `docs/formats/goodnotes.md` and `docs/formats/notability.md`)
and a changelog section. When an app updates, the discipline is: re-run
the relevant corpus cases against the new version before trusting the old
spec for it, and add a changelog entry either confirming no drift or
recording what changed. A format doc without a recent "verified against"
date for the app's current version should be treated as possibly stale,
not as ground truth.

## See also

- `docs/corpus-protocol.md` — the controlled-corpus procedure this
  workflow's step 3 refers to.
- `docs/formats/goodnotes.md`, `docs/formats/notability.md` — worked
  examples of confidence-marked format docs produced by this workflow.
- `docs/ir.md` — what a reader is expected to produce once decoding is
  trusted enough to write one.
