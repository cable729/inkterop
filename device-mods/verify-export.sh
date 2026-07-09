#!/bin/bash
# Check whether every page of a PDF has the same (standard) page size.
# Uses macOS PDFKit via JXA - no dependencies.
#
# Usage: ./verify-export.sh <file.pdf>
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: $0 <file.pdf>"; exit 1; }
PDF="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"

osascript -l JavaScript - "$PDF" <<'EOF'
ObjC.import("Quartz");
function run(argv) {
  const doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(argv[0]));
  if (doc.isNil()) return "error: cannot open " + argv[0];
  const n = doc.pageCount;
  const sizes = {};
  let out = "";
  for (let i = 0; i < n; i++) {
    const r = doc.pageAtIndex(i).boundsForBox($.kPDFDisplayBoxMediaBox);
    const key = r.size.width.toFixed(1) + " x " + r.size.height.toFixed(1);
    sizes[key] = (sizes[key] || 0) + 1;
    out += "page " + (i + 1) + ": " + key + " pt\n";
  }
  const uniq = Object.keys(sizes);
  out += "----\n";
  out += uniq.length === 1
    ? "UNIFORM: all " + n + " pages are " + uniq[0] + " pt"
    : "NON-UNIFORM: " + uniq.length + " distinct sizes across " + n + " pages: " +
      uniq.map(k => k + " (" + sizes[k] + ")").join(", ");
  return out;
}
EOF
