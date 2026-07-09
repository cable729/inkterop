# Ecosystem research (July 2026)

Condensed from three deep-research passes (2026-07-08). Maintenance claims
are as of that date.

## Format & rendering tools

- **rmscene** (github.com/ricklupton/rmscene) — THE v6 parser. v0.8.0
  (Apr 2026), active. Per-point x/y/speed/direction/width/pressure, text,
  Paper Pro colors. Experimental write support (`write_blocks`).
- **rmc** (github.com/ricklupton/rmc) — .rm → SVG/PDF/MD on rmscene. Its pen
  width formulas are rM2-era — see `format-notes.md` for why we don't use them.
- **Scrybbling-together/remarks** — maintained fork of lucasrla/remarks for
  v6; outputs xopp too. Original remarks is ≤2.15 only.
- **RCU** (davisr.me/projects/rcu) — paid/source-available manager; supports
  Paper Pro + fw 3.27; consensus best turnkey exporter (fidelity benchmark).
- **drawj2d** (drawj2d.sourceforge.io) 1.4.0 — PDF/SVG → **native editable
  .rm/.rmdoc ink**, full Paper Pro color (scale 0.8 for RMPP). The only
  "back into reMarkable as editable ink" path. Wrapper: pdf2rmnotebook.

## Cloud & sync

- **ddvk/rmapi** v0.0.34 (May 2026) — working cloud CLI (list/get/put/mkdir).
  reMarkable published a data-corruption warning about third-party writes:
  reads fine, writes only after `rmapi` backup.
- **rmfakecloud** (ddvk) — self-hosted cloud, supports Paper Pro + sync
  protocols ≤3.27.1. Not needed for our architecture but good escape hatch.
- **Desktop app cache** — full local xochitl mirror (our source of truth;
  see CLAUDE.md). Note: one research pass wrongly claimed it's blob-format;
  direct inspection shows plain xochitl.
- **USB web UI** (`http://10.11.99.1`, stock firmware): per-doc
  `/download/{uuid}/pdf` and `/rmdoc` (fw 3.9+); one doc at a time.

## Paper Pro modding (NOT the rM1/rM2 stack)

- Toltec, ddvk/remarkable-hacks, Oxide: **do not support Paper Pro**.
- The live stack: **XOVI** (runtime injection; rootfs is read-only with
  non-persistent overlays) + **qt-resource-rebuilder** + **qmldiff .qmd**
  patches + **xovi-tripletap** (persistence via triple power-press;
  systemd unit on ROOT partition — the rollback hazard) + **remagic**
  (one-line installer) + **Vellum** (package manager).
- **rmitchellscott/xovi-qmd-extensions** — firmware-matched folders
  (3.20–3.27). Key extensions vendored in `device-mods/vendor/`:
  `disableInfiniteScroll` (finite canvas incl. landscape branch — our
  page-growth fix candidate), `createPagesPaperProSize` (fixed 1620x2160
  new pages), `ghostbuster` (full-refresh gesture), `quickSettingsScreenshot`.
- Dev mode: Settings → General → ... → Developer Mode. WIPES the device;
  SSH root over USB at 10.11.99.1 (password in About → Copyrights);
  `rm-ssh-over-wlan on` for WiFi. Disabling dev mode (Recovery app) wipes
  again to stock — but only the DATA partition, hence rollback.sh first.
- No hidden xochitl.conf flag for page growth is known.

## iPad interop (the honest picture)

- **GoodNotes**: closed, encrypted-ish protobuf+LZ4 zip. First-ever stroke
  parser `franzthiemann/goodparse` appeared 2026-06-29 (one-way → xopp,
  proof-of-concept). No injection path. Avoid as interop partner.
- **Notability**: old zip+plist format was reverse-engineered (jvns 2018,
  svg2notability could INJECT ink; still parseable ~2023) but Ginger Labs
  began replacing it with an uncracked cloud/collab format (Aug 2025).
  Moving target; avoid.
- **Saber** (github.com/saber-notes/saber) — open-source Flutter notes app
  on iOS; open documented formats (.sbn2 BSON, .sba zip); WebDAV/Nextcloud
  sync. The only candidate open on both ends. UNKNOWN: writing feel — test
  before committing.
- **Nebo/MyScript** — best handwriting feel + HWR; exports SVG; but .nebo
  un-reversed, JIIX (open JSON ink) only in their SDK, no automation hooks.
  One-way street: Nebo SVG → drawj2d → editable rM ink works.
- **OneNote** — most extractable mainstream app: MS-ONE/ONESTORE documented,
  ink is ISF (decoded by `msiemens/onenote.rs`), and **Microsoft Graph
  returns page ink as InkML** (`GET .../pages/{id}/content?includeinkML=true`).
  Mediocre Pencil feel; injection back is hard.
- **Open ink standards**: W3C InkML (2011), MS-ISF, Wacom Universal Ink
  Model (protobuf/RIFF, WILL SDK). Only OneNote actually surfaces one.
  Practically open containers: .rm, .xopp, Excalidraw JSON, Saber .sbn2.
- **Universal truth**: editable-ink round-trip between reMarkable and
  GoodNotes/Notability does not exist; PDF is the interchange currency.
  reMarkable→iPad = annotate-on-top; iPad→reMarkable can be EDITABLE via
  vector PDF/SVG → drawj2d.

## Community

reMarkable Discord (discord.com/invite/u3P9sDW), remarkable.guide (+ Discord
archive), github.com/reHackable/awesome-reMarkable, Nilorea Studio guides.
