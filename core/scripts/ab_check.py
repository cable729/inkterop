"""Whole-library A/B fidelity check for renderer refactors.

Snapshot the normalized drawing ops of every rendered document, then compare
two snapshots taken with different code versions:

    uv run python scripts/ab_check.py snapshot /tmp/ab/before
    # ... refactor ...
    uv run python scripts/ab_check.py snapshot /tmp/ab/after
    uv run python scripts/ab_check.py compare /tmp/ab/before /tmp/ab/after

Snapshots are op dumps (like tests/test_golden_remarkable.py), so PDF
metadata nondeterminism (CreationDate, doc ID) doesn't cause false diffs.
Read-only on the desktop cache.
"""
from __future__ import annotations

import gzip
import json
import sys
import tempfile
import time
from pathlib import Path

import pikepdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rminterop.config import Config  # noqa: E402
from rminterop.library import Library  # noqa: E402
from rminterop.render import render_notebook  # noqa: E402


def _jsonable(obj):
    if isinstance(obj, pikepdf.Name):
        return str(obj)
    if isinstance(obj, pikepdf.String):
        return bytes(obj).decode("latin-1")
    if isinstance(obj, pikepdf.Array):
        return [_jsonable(o) for o in obj]
    if isinstance(obj, pikepdf.Dictionary):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (int, float)) or hasattr(obj, "__float__"):
        f = float(obj)
        return int(f) if f == int(f) else round(f, 4)
    return str(obj)


def pdf_ops(pdf_path: Path) -> list:
    pages = []
    with pikepdf.open(pdf_path) as pdf:
        for page in pdf.pages:
            ops = [
                [str(op.operator), [_jsonable(o) for o in op.operands]]
                for op in pikepdf.parse_content_stream(page)
            ]
            pages.append({
                "mediabox": [round(float(v), 4) for v in page.mediabox],
                "ops": ops,
            })
    return pages


def snapshot(out_dir: Path) -> None:
    cfg = Config.load()
    lib = Library()
    out_dir.mkdir(parents=True, exist_ok=True)
    n = failed = 0
    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        for doc in lib.documents():
            pages = [doc.dir / f"{u}.rm" for u in doc.page_uuids]
            pdf = Path(tmp) / f"{doc.uuid}.pdf"
            try:
                render_notebook(pages, pdf, doc.orientation == "landscape",
                                cfg.render_config(), templates=doc.page_templates)
                ops = pdf_ops(pdf)
            except Exception as e:  # noqa: BLE001
                print(f"FAIL {doc.name}: {e}", file=sys.stderr)
                failed += 1
                continue
            with gzip.open(out_dir / f"{doc.uuid}.json.gz", "wt") as f:
                json.dump({"name": doc.name, "pages": ops}, f,
                          separators=(",", ":"))
            n += 1
            print(f"  {doc.name}", flush=True)
    print(f"snapshot: {n} docs ({failed} failed) in "
          f"{time.time() - t0:.0f}s -> {out_dir}")


def compare(a_dir: Path, b_dir: Path) -> int:
    a_files = {p.name for p in a_dir.glob("*.json.gz")}
    b_files = {p.name for p in b_dir.glob("*.json.gz")}
    same = diff = 0
    for name in sorted(a_files & b_files):
        with gzip.open(a_dir / name, "rt") as f:
            a = json.load(f)
        with gzip.open(b_dir / name, "rt") as f:
            b = json.load(f)
        if a["pages"] == b["pages"]:
            same += 1
            continue
        diff += 1
        detail = "page count changed"
        for i, (pa, pb) in enumerate(zip(a["pages"], b["pages"])):
            if pa == pb:
                continue
            if pa["mediabox"] != pb["mediabox"]:
                detail = f"page {i + 1} mediabox {pa['mediabox']} != {pb['mediabox']}"
            else:
                for j, (oa, ob) in enumerate(zip(pa["ops"], pb["ops"])):
                    if oa != ob:
                        detail = f"page {i + 1} op {j}: {oa!r} != {ob!r}"
                        break
                else:
                    detail = (f"page {i + 1} op count "
                              f"{len(pa['ops'])} != {len(pb['ops'])}")
            break
        print(f"DIFF {a['name']}: {detail}")
    for name in sorted(a_files - b_files):
        print(f"MISSING-IN-B {name}")
    for name in sorted(b_files - a_files):
        print(f"MISSING-IN-A {name}")
    print(f"compare: {same} identical, {diff} different, "
          f"{len(a_files ^ b_files)} unmatched")
    return 1 if diff or (a_files ^ b_files) else 0


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "snapshot":
        snapshot(Path(sys.argv[2]))
        return 0
    if len(sys.argv) >= 4 and sys.argv[1] == "compare":
        return compare(Path(sys.argv[2]), Path(sys.argv[3]))
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
