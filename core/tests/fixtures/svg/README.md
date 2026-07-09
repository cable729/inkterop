# SVG fixtures (hand-made, CC0)

Both files are authored by hand for this repo — schema-by-us, not app
exports. CC0.

- `tiny-generic.svg` — generic-reader coverage: path commands
  (M/L/H/V/l, cubic C), a `translate+scale` transform, inline `style=`
  override, `polyline`, `line`, 3-digit hex color, plus content that
  must be skipped (`defs`, `<style>` block, a fill-only path, an
  A(rc)-command path). Expected: 5 strokes.
- `write-mini.svg` — Stylus Labs Write-FLAVORED file following the
  structure documented in the styluslabs/templates README (root
  `svg#write-document`, per-page `svg.write-page` >
  `g.write-content` with page-setup attrs, `g.ruleline` to skip).
  It is not a Write export; real Write samples live in the gitignored
  `corpus/third-party/styluslabs-write/`. Expected: 2 pages, strokes
  2 + 1 (rulelines and pagerects skipped), page 2 uses an unobserved
  `write-stroke-highlight` class to exercise the unknown-tool path.
