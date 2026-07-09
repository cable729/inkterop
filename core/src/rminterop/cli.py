"""rminterop CLI: mirror | watch | render | ls"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rminterop")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--cache-dir", type=Path, help="override library cache dir")
    parser.add_argument("--config", type=Path, help="config file path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("mirror", help="one incremental mirror pass")
    w = sub.add_parser("watch", help="watch the cache and mirror continuously")
    w.add_argument("--debounce", type=float, default=30.0)
    r = sub.add_parser("render", help="render one document to PDF")
    r.add_argument("name", help="document name or uuid")
    r.add_argument("out", type=Path, nargs="?", help="output PDF path")
    sub.add_parser("ls", help="list library documents")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")

    from .config import Config
    cfg = Config.load(args.config)

    if args.cmd == "mirror":
        from .mirror import mirror_once
        s = mirror_once(cfg, args.cache_dir)
        print(f"rendered {s['rendered']}, unchanged {s['skipped']}, "
              f"failed {s['failed']}, removed {s['removed']} "
              f"({s['seconds']}s, {s['documents']} docs)")
        return 1 if s["failed"] else 0

    if args.cmd == "watch":
        from .mirror import watch
        watch(cfg, args.cache_dir, args.debounce)
        return 0

    from .library import Library
    lib = Library(args.cache_dir)

    if args.cmd == "ls":
        for d in lib.documents():
            print(f"{lib.path_of(d) / d.name}  [{d.file_type}, {d.orientation}, "
                  f"{len(d.page_uuids)}p]")
        return 0

    if args.cmd == "render":
        from .render import render_notebook
        doc = lib.find(args.name)
        if doc is None or doc.is_folder:
            print(f"not found: {args.name}", file=sys.stderr)
            return 1
        out = args.out or Path(f"{doc.name}.pdf")
        pages = [doc.dir / f"{u}.rm" for u in doc.page_uuids]
        render_notebook(pages, out, doc.orientation == "landscape",
                        cfg.render_config(), templates=doc.page_templates)
        print(out)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
