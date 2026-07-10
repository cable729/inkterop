# iPad calibration pages — drawing script

One page per app, ~2–3 minutes each. These pages measure each app's
*rendering rule* (how pen channels + tool settings become on-screen
width/opacity), which is what makes strokes look right when converted
between apps. Same script every time so the analysis code is reusable.

## The script (same for every app)

Work top-to-bottom in rows, **one row per tool, in the order the tools
appear in the app's toolbar** (that ordering is how rows map to tools —
no labels needed). Draw with the Apple Pencil, left→right, rows clearly
separated. Per row, five probes side by side:

1. **Baseline** — ~4 cm horizontal line, constant comfortable pressure,
   slow and steady.
2. **Pressure ramp** — ~4 cm horizontal line, feather-light start →
   pressing hard at the end.
3. **Speed sweep** — ~4 cm horizontal line, constant pressure, very slow
   start → as fast as you can at the end.
4. **Tilt pair** — two short (~1.5 cm) strokes side by side: pencil held
   vertical, then tilted ~45°, same pressure.
5. **Dot + cross** — one single tap, then two short strokes crossing in
   an X.

## Per-app rows (skip erasers / lasso / shape tools)

- **GoodNotes**: one row per pen style in the pen picker (fountain, ball,
  brush — whatever it offers) at default size; a highlighter row; then
  TWO extra fountain-pen rows at the **smallest** and **largest** size
  setting; finally one baseline stroke in **red** anywhere below.
- **Notability**: same pattern (every pen style, highlighter, two
  size-extreme rows for the main pen) + one **red** baseline stroke —
  the red stroke settles the open R-vs-G color byte-order question in
  `docs/formats/notability.md`.
- **Saber**: fountain pen, ballpoint, pencil, highlighter rows + two
  size-extreme rows.
- **Nebo** (if installed): every pen row + highlighter, same pattern.
- **Supernote** (when the device is back): one row per pen type at
  default width + size extremes for one pen.

## After drawing each page

Export **PDF (highest quality / vector if offered)** AND share the
**native file** (GoodNotes: share → .goodnotes; Notability: share →
note file; Saber: the .sbn2; Nebo: .nebo). Both go into `corpus/` with a
manifest row (app version + iPadOS version).

If tilt turns out to matter for a tool, a follow-up mini-page may be
requested for just that tool (8 short strokes tilted in 8 compass
directions). Not needed up front.
