# tools/apps — driving Mac note apps for RE experiments

`appctl.py` wraps per-app import/export/reset actions so an experiment is
one shell pipeline. UI recipes get recorded here as they're discovered.

## Safety

- **reMarkable desktop, OneNote, Apple Notes hold real user data.**
  `appctl` refuses snapshot/restore/reset for them. Imports into
  reMarkable go through the app UI (drag-and-drop / File → Import) only —
  never its cache directory.
- GoodNotes / Notability / Saber / Xournal++ here are trial/scratch
  installs with nothing of value; they may be freely reset.
- Free-trial caps: GoodNotes free tier allows 3 notebooks. Every
  experiment session should end with the imported scratch notes deleted
  (or `appctl reset`) so the cap never blocks the next round.

## Per-app recipes

### Xournal++ — fully headless ✅ (v1.3.5)

```sh
appctl.py export-pdf xournalpp doc.xopp out.pdf   # xournalpp -p
appctl.py export-img xournalpp doc.xopp out.png   # xournalpp -i, one per page
```

No UI needed for the whole loop. This is the reference app for proving the
`[app] → [inkz] → [pinned render]` vs `[app].export()` comparison.

### GoodNotes / Notability / Saber — UI-driven (recipes TBD)

`appctl import <app> <file>` opens the file via `open -a` (triggers the
app's import path). Export recipes are not menu-scriptable in the obvious
way yet; until a recipe is recorded here, exports are driven interactively
(computer-use) with the results dropped into `corpus/validate/`.

Container paths (for snapshot/restore, already wired):

- GoodNotes: `~/Library/Containers/com.goodnotesapp.x`
- Notability: `~/Library/Containers/com.gingerlabs.Notability`
- Saber: `~/Library/Containers/com.adilhanney.saber`

Discovery TODO list (fill in as sessions happen):
- [ ] GoodNotes: import result location in container; export-PDF menu path;
      whether `System Events` menu clicking works (app is Catalyst).
- [ ] Notability: same.
- [ ] Saber: app data is plain files in the container (open format) — the
      re-saved `.sbn2` can be read straight from disk after an edit;
      note the path.
