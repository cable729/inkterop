#!/bin/bash
# Install the landscape-fix qmd extensions onto a Paper Pro that already has
# XOVI + qt-resource-rebuilder installed (e.g. via remagic).
#
# Usage: ./install.sh [host]        (default host: 10.11.99.1 = USB)
set -euo pipefail

HOST="${1:-10.11.99.1}"
RM="root@$HOST"
DEST="/home/root/xovi/exthome/qt-resource-rebuilder"
VENDOR="$(cd "$(dirname "$0")/vendor" && pwd)"

echo "==> Checking device firmware version..."
VERSION=$(ssh "$RM" "cat /etc/version 2>/dev/null || grep -o 'REMARKABLE_RELEASE_VERSION=.*' /usr/share/remarkable/update.conf 2>/dev/null | cut -d= -f2" | tr -d '\r "')
echo "    reported: $VERSION"

# Match major.minor against a vendored folder
MM=$(echo "$VERSION" | grep -oE '^3\.[0-9]+' || true)
if [ -z "$MM" ] || [ ! -d "$VENDOR/$MM" ]; then
  echo "!!  No vendored qmd folder for firmware '$VERSION'."
  echo "    Available: $(ls "$VENDOR" | grep '^3\.' | tr '\n' ' ')"
  echo "    If the device is older than 3.26, update it first — disableInfiniteScroll"
  echo "    (the landscape fix) only exists for 3.26+."
  exit 1
fi
if [ ! -f "$VENDOR/$MM/disableInfiniteScroll.qmd" ]; then
  echo "!!  Firmware $MM has no disableInfiniteScroll.qmd — update the device to 3.26+."
  exit 1
fi

echo "==> Verifying XOVI + qt-resource-rebuilder are installed..."
ssh "$RM" "test -d $DEST" || {
  echo "!!  $DEST missing. Install XOVI first (remagic one-liner):"
  echo "    curl -fsSL https://raw.githubusercontent.com/maximerivest/remagic/main/get.sh | sh"
  exit 1
}

echo "==> Copying extensions for firmware $MM..."
scp "$VENDOR/$MM/"*.qmd "$RM:$DEST/"
ssh "$RM" "ls -la $DEST/"

echo "==> Restarting XOVI (restarts xochitl)..."
if ssh "$RM" "systemctl is-active --quiet xovi-tripletap 2>/dev/null"; then
  ssh "$RM" "systemctl restart xovi-tripletap" || true
fi
echo "    If the UI did not restart with XOVI active, triple-press the power button."

cat <<'EOF'

Done. On the device:
  1. Settings -> Display: enable "Finite canvas" (and try both states of the
     portrait-margins toggle to see which landscape behavior you prefer).
  2. New pages are now created at fixed Paper Pro dimensions (1620x2160).
  3. 5-finger tap in a document = full screen refresh (ghostbuster - also a
     good test of whether your ghosting clears with refreshes).
  4. Quick settings has a screenshot button - use it to capture Ballpoint
     strokes for renderer-fidelity reference, then grab PNGs from
     /home/root/screenshots (or as documented by the extension).

Now run the pivotal test: new LANDSCAPE notebook, write past one screen height,
export to PDF, and check page sizes with ./verify-export.sh <file.pdf>.
EOF
