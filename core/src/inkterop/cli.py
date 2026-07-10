"""inkterop CLI: mirror | watch | render | ls | convert | inspect"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="inkterop")
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
    c = sub.add_parser("convert", help="convert between note/display formats")
    c.add_argument("input", help="input file, or library document name/uuid")
    c.add_argument("output", type=Path, help="output file (format by extension)")
    c.add_argument("--fidelity", choices=["exact", "native", "raw"],
                   default="exact",
                   help="exact: source app's look; native: target restyles "
                        "semantically; raw: per-point pen data")
    c.add_argument("--pages", help="page selection, e.g. 1-3,7")
    c.add_argument("--experimental", action="store_true",
                   help="allow writers not yet validated against their app")
    c.add_argument("--force", action="store_true",
                   help="skip output-path safety checks")
    v = sub.add_parser("visualdiff",
                       help="pixel-compare two PDFs page by page")
    v.add_argument("a", type=Path, help="reference PDF")
    v.add_argument("b", type=Path, help="candidate PDF")
    v.add_argument("--mode", choices=["strict", "registered"],
                   default="strict",
                   help="strict: same raster size; registered: crop to ink "
                        "bbox and rescale (cross-app)")
    v.add_argument("--dpi", type=int, default=None)
    v.add_argument("--tolerance", type=int, default=None,
                   help="per-channel 0-255 pixel tolerance")
    v.add_argument("--report", type=Path, default=None,
                   help="directory for .diff.png overlays")
    i = sub.add_parser("inspect", help="summarize a note file's parsed content")
    i.add_argument("input", help="input file, or library document name/uuid")
    i.add_argument("--json", action="store_true", dest="as_json",
                   help="dump the full IR as JSON to stdout")

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

    if args.cmd == "convert":
        from .convert import ConvertError, convert
        from .formats.base import Fidelity
        try:
            convert(Path(args.input), args.output,
                    fidelity=Fidelity(args.fidelity), pages=args.pages,
                    experimental=args.experimental, force=args.force,
                    cache_dir=args.cache_dir)
        except ConvertError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(args.output)
        return 0

    if args.cmd == "visualdiff":
        from .visual.diff import DEFAULT_PIXEL_TOLERANCE, compare_pdfs
        results = compare_pdfs(
            args.a, args.b, mode=args.mode, dpi=args.dpi,
            pixel_tolerance=(args.tolerance if args.tolerance is not None
                             else DEFAULT_PIXEL_TOLERANCE),
            report_dir=args.report)
        from .visual.raster import page_count
        ca, cb = page_count(args.a), page_count(args.b)
        if ca != cb:
            print(f"page-count mismatch: {ca} vs {cb} "
                  f"(comparing first {min(ca, cb)})", file=sys.stderr)
        worst = 1.0
        for i, r in enumerate(results, 1):
            note = f"  [{r.aspect_warning}]" if r.aspect_warning else ""
            print(f"p{i}: match {r.match_ratio:.4%}  ink-match "
                  f"{r.ink_match_ratio:.4%}  ({r.n_diff_pixels} px differ, "
                  f"{r.n_ink_pixels} ink px){note}")
            worst = min(worst, r.ink_match_ratio)
        print(f"worst ink-match: {worst:.4%}")
        return 0

    if args.cmd == "inspect":
        from .convert import ConvertError, read_input
        try:
            doc = read_input(Path(args.input), cache_dir=args.cache_dir)
        except ConvertError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        if args.as_json:
            from .ir import serialize
            print(serialize.dumps(doc, indent=2))
            return 0
        print(f"{doc.title or args.input}  [{doc.format_id}, "
              f"{doc.orientation}, {len(doc.pages)}p]")
        for n, page in enumerate(doc.pages, 1):
            strokes = list(page.strokes())
            tools: dict[str, int] = {}
            channels: set[str] = set()
            points = 0
            for s in strokes:
                tools[s.tool.family.value] = tools.get(s.tool.family.value, 0) + 1
                channels.update(ch.value for ch in s.channels)
                points += len(s)
            b = page.bounds
            bg = type(page.background).__name__ if page.background else "none"
            tstr = ", ".join(f"{k}x{v}" for k, v in sorted(tools.items())) or "empty"
            print(f"  p{n}: {len(strokes)} strokes ({tstr}), {points} pts, "
                  f"bounds [{b.x_min:.0f},{b.y_min:.0f}..{b.x_max:.0f},"
                  f"{b.y_max:.0f}], bg={bg}")
            if channels:
                print(f"      channels: {', '.join(sorted(channels))}")
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
