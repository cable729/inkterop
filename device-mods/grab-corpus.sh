#!/bin/bash
# Back up the device's raw notebook store to ./corpus/ (test data for the
# renderer, and a safety net before any experiment).
#
# Usage: ./grab-corpus.sh [host]        (default host: 10.11.99.1 = USB)
set -euo pipefail
HOST="${1:-10.11.99.1}"
DEST="$(dirname "$0")/corpus/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"
echo "==> Copying /home/root/.local/share/remarkable/xochitl/ -> $DEST"
scp -r "root@$HOST:/home/root/.local/share/remarkable/xochitl/" "$DEST/"
du -sh "$DEST"
echo "Done. (corpus/ is gitignored - it contains your actual notes.)"
