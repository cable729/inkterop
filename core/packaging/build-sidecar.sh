#!/usr/bin/env bash
# Build the PyInstaller sidecar and drop it where Tauri's externalBin
# expects it: app/src-tauri/binaries/inkterop-daemon-<target-triple>.
#
# Usage: packaging/build-sidecar.sh [target-triple]
# (defaults to the host triple as rustc reports it)
set -euo pipefail

CORE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_BIN_DIR="$CORE_DIR/../app/src-tauri/binaries"
TRIPLE="${1:-$(rustc -vV | sed -n 's/^host: //p')}"

cd "$CORE_DIR"
uv sync --group packaging
uv run --group packaging pyinstaller \
    --distpath packaging/dist --workpath packaging/build \
    --noconfirm packaging/inkterop-daemon.spec

mkdir -p "$APP_BIN_DIR"
EXT=""
case "$TRIPLE" in *windows*) EXT=".exe" ;; esac
cp "packaging/dist/inkterop-daemon$EXT" \
   "$APP_BIN_DIR/inkterop-daemon-$TRIPLE$EXT"
echo "sidecar: $APP_BIN_DIR/inkterop-daemon-$TRIPLE$EXT"

# Smoke test: the frozen binary must answer a JSON-RPC ping (capture the
# full output; an early-exiting reader would break the daemon's pipe).
OUT="$(printf '{"jsonrpc":"2.0","id":1,"method":"ping"}\n' \
  | "$APP_BIN_DIR/inkterop-daemon-$TRIPLE$EXT" daemon --no-watch)"
case "$OUT" in
  *'"id":1'*'"pong":true'*|*'"pong":true'*'"id":1'*)
    echo "sidecar smoke test: OK" ;;
  *)
    echo "sidecar smoke test FAILED; output was:"; echo "$OUT"; exit 1 ;;
esac
