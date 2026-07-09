# device-mods — XOVI landscape fix for the Paper Pro

Goal: make landscape notebooks keep a **fixed standard page size** instead of
growing vertically, so exports come out uniform. Runs entirely via
[XOVI](https://github.com/asivery/xovi) runtime injection + qmldiff `.qmd`
patches — no permanent changes to the OS.

Extensions vendored from
[rmitchellscott/xovi-qmd-extensions](https://github.com/rmitchellscott/xovi-qmd-extensions)
(GPL-3.0, commit in `vendor/UPSTREAM_COMMIT`):

| Extension | What it does |
|---|---|
| `disableInfiniteScroll` | **The landscape fix.** Clamps scrolling/zoom to page boundaries (settings: *Finite canvas* + portrait-margins toggle in Display settings). |
| `createPagesPaperProSize` | Every new page is created at fixed 1620×2160. |
| `ghostbuster` | 5-finger tap = full screen refresh. Diagnostic for the ghosting issue: if refreshes clear it, it may not be (only) hardware. |
| `quickSettingsScreenshot` | On-device screenshots — capture Ballpoint stroke references for the renderer fidelity work. |

**Firmware note:** `disableInfiniteScroll` exists only for **3.26+**. Update the
device to the latest OS before starting.

## The 4-day plan

**Prep (before rooting)**
- [ ] Confirm cloud sync is green (Settings → Account) — enabling dev mode wipes the device.
- [ ] Record firmware version (Settings → General → Software); update to latest.

**Day 1 — root + baseline**
- [ ] Enable developer mode (Settings → General → Paper Tablet → Software → Advanced). Device wipes; re-onboard and let notes restore from cloud.
- [ ] Get SSH password: Settings → General → Help → About → Copyrights and licenses (GPLv3 section). Test `ssh root@10.11.99.1` over USB.
- [ ] Install XOVI stack: `curl -fsSL https://raw.githubusercontent.com/maximerivest/remagic/main/get.sh | sh`
- [ ] `./grab-corpus.sh` — raw backup + renderer test data.
- [ ] **Baseline bug repro:** new landscape notebook, write past one screen height, export PDF, run `./verify-export.sh` → expect NON-UNIFORM.
- [ ] Cheap parallel check: `ssh root@10.11.99.1 "grep -iE 'page|continuous|canvas|scroll|height' ~/.config/remarkable/xochitl.conf"` (hidden flags — low probability).

**Day 2 — apply + the pivotal test**
- [ ] `./install.sh`
- [ ] Enable *Finite canvas* in Settings → Display.
- [ ] Repeat the landscape write+export test → `./verify-export.sh`.
- [ ] **UNIFORM?** Done — record settings, note both margin-toggle behaviors.
- [ ] Also: capture on-device screenshots of Ballpoint strokes (fidelity reference); test whether 5-finger refresh clears the ghosting (note result for the repair claim).

**Day 3 — iterate (only if exports still non-uniform)**
- [ ] The view is clamped but export height still follows stored content bounds → edit the landscape branch of `disableInfiniteScroll.qmd` to pin `pageHeight`/content bounds like the portrait path. Loop: edit → `scp` → restart XOVI → export → `./verify-export.sh`.
- [ ] Commit any working patch here as `patches/disableInfiniteScroll-landscape-export.qmd`.

**Day 4 — warranty-clean rollback (ORDER MATTERS)**
- [ ] `./rollback.sh` — uninstalls tripletap (removes the root-partition systemd unit), disables XOVI, deletes `/home/root/xovi*`, verifies `/etc/systemd/system` is clean.
- [ ] Only after rollback.sh reports clean: disable developer mode via the [Recovery application](https://support.remarkable.com/s/article/Software-recovery).
- [ ] Verify stock boot, no dev-mode warning, cloud restore OK → ship it.

## Scripts

- `install.sh [host]` — detect firmware, push matching qmds, restart XOVI.
- `verify-export.sh <pdf>` — page-size uniformity check (macOS, no deps).
- `grab-corpus.sh [host]` — scp the raw xochitl store into `corpus/` (gitignored).
- `rollback.sh [host]` — full clean removal; run before disabling dev mode.

Default host is `10.11.99.1` (USB). For WiFi SSH: `ssh root@10.11.99.1 rm-ssh-over-wlan on`, then pass the device's WiFi IP.
