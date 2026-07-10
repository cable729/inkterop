#!/usr/bin/env python3
"""appctl — drive Mac note apps for import/export experiments.

One command per action so an RE experiment is scriptable end-to-end:

    appctl.py export-pdf xournalpp doc.xopp out.pdf   # headless, no UI
    appctl.py export-img xournalpp doc.xopp out.png   # headless, per page
    appctl.py import goodnotes probe.goodnotes        # opens the import UI
    appctl.py snapshot goodnotes / restore goodnotes  # container backup
    appctl.py reset goodnotes                         # quit + restore

Safety rules (enforced, not advisory):
- reMarkable desktop, OneNote and Apple Notes hold real user data. They are
  NOT resettable/restorable here, ever. Imports into reMarkable go through
  the app UI only (never its cache directory).
- snapshot/restore only touch the sandboxed app container of the listed
  free-trial apps, and restore refuses to run without a snapshot.

UI recipes (menu scripting) are discovered per app and recorded in
README.md next to this file; anything not scriptable falls back to a
human/agent driving the app (computer-use).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

HOME = Path.home()
SNAPSHOTS = HOME / ".cache" / "inkterop" / "app-snapshots"


@dataclass(frozen=True)
class App:
    key: str
    app_name: str                 # for `open -a` / System Events
    bundle_id: str
    container: Path | None        # sandbox container (None = not sandboxed)
    resettable: bool              # False = holds real user data, hands off


APPS = {
    "xournalpp": App("xournalpp", "Xournal++", "com.github.xournalpp.xournalpp",
                     None, resettable=False),
    "goodnotes": App("goodnotes", "Goodnotes", "com.goodnotesapp.x",
                     HOME / "Library/Containers/com.goodnotesapp.x",
                     resettable=True),
    "notability": App("notability", "Notability", "com.gingerlabs.Notability",
                      HOME / "Library/Containers/com.gingerlabs.Notability",
                      resettable=True),
    "saber": App("saber", "Saber", "com.adilhanney.saber",
                 HOME / "Library/Containers/com.adilhanney.saber",
                 resettable=True),
    "remarkable": App("remarkable", "reMarkable", "com.remarkable.desktop",
                      None, resettable=False),  # REAL DATA — read/import only
}

XOURNALPP_BIN = "/Applications/Xournal++.app/Contents/MacOS/xournalpp"


def _app(key: str) -> App:
    if key not in APPS:
        sys.exit(f"unknown app {key!r}; known: {', '.join(sorted(APPS))}")
    return APPS[key]


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


def _quit(app: App) -> None:
    subprocess.run(["osascript", "-e", f'tell application id "{app.bundle_id}" to quit'],
                   capture_output=True)
    time.sleep(2)


def cmd_import(app: App, file: Path) -> int:
    """Open a file with the app (the app decides import semantics)."""
    _run(["open", "-a", app.app_name, str(file)])
    print(f"opened {file.name} in {app.app_name}")
    return 0


def cmd_export_pdf(app: App, doc: Path, out: Path) -> int:
    if app.key == "xournalpp":
        _run([XOURNALPP_BIN, str(doc), "-p", str(out)],
             capture_output=True)
        print(out)
        return 0
    sys.exit(f"{app.key}: no scripted PDF export recipe yet (see README.md); "
             f"drive the app UI instead")


def cmd_export_img(app: App, doc: Path, out: Path) -> int:
    if app.key == "xournalpp":
        _run([XOURNALPP_BIN, str(doc), "-i", str(out)],
             capture_output=True)
        print(out)
        return 0
    sys.exit(f"{app.key}: no scripted image export recipe yet")


def _snapshot_dir(app: App) -> Path:
    return SNAPSHOTS / app.key


def cmd_snapshot(app: App) -> int:
    if not app.resettable:
        sys.exit(f"{app.key} holds real user data; snapshot/restore disabled")
    assert app.container is not None
    if not app.container.exists():
        sys.exit(f"container not found: {app.container}")
    _quit(app)
    dest = _snapshot_dir(app)
    if dest.exists():
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        dest.rename(dest.with_name(f"{app.key}-old-{stamp}"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(app.container, dest, symlinks=True)
    print(f"snapshot: {app.container} -> {dest}")
    return 0


def cmd_restore(app: App) -> int:
    if not app.resettable:
        sys.exit(f"{app.key} holds real user data; snapshot/restore disabled")
    assert app.container is not None
    src = _snapshot_dir(app)
    if not src.exists():
        sys.exit(f"no snapshot for {app.key}; run snapshot first")
    _quit(app)
    if app.container.exists():
        shutil.rmtree(app.container)
    shutil.copytree(src, app.container, symlinks=True)
    print(f"restored {app.container} from {src}")
    return 0


def cmd_reset(app: App) -> int:
    return cmd_restore(app)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="appctl")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, extra in [("import", ["file"]), ("export-pdf", ["doc", "out"]),
                        ("export-img", ["doc", "out"]), ("snapshot", []),
                        ("restore", []), ("reset", [])]:
        s = sub.add_parser(name)
        s.add_argument("app")
        for a in extra:
            s.add_argument(a, type=Path)
    args = p.parse_args(argv)
    app = _app(args.app)
    if args.cmd == "import":
        return cmd_import(app, args.file)
    if args.cmd == "export-pdf":
        return cmd_export_pdf(app, args.doc, args.out)
    if args.cmd == "export-img":
        return cmd_export_img(app, args.doc, args.out)
    if args.cmd == "snapshot":
        return cmd_snapshot(app)
    if args.cmd == "restore":
        return cmd_restore(app)
    if args.cmd == "reset":
        return cmd_reset(app)
    return 2


if __name__ == "__main__":
    sys.exit(main())
