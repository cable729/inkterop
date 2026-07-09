#!/bin/bash
# Warranty-clean rollback. Run BEFORE disabling developer mode.
#
# Order matters: xovi-tripletap writes a systemd unit onto the ROOT partition
# (/etc/systemd/system/xovi-tripletap.service). Factory reset / disabling dev
# mode only wipes the DATA partition, so that file would survive as a
# warranty-visible trace unless removed here first.
#
# Usage: ./rollback.sh [host]        (default host: 10.11.99.1 = USB)
set -euo pipefail

HOST="${1:-10.11.99.1}"
RM="root@$HOST"

echo "This removes ALL mods from the device at $HOST."
read -r -p "Continue? [y/N] " ans
[ "$ans" = "y" ] || exit 1

echo "==> 1/5 Running xovi-tripletap uninstaller (removes root-partition service)..."
ssh "$RM" "sh /home/root/xovi-tripletap/uninstall.sh" || \
  echo "    (uninstaller missing or already run - will verify below)"

echo "==> 2/5 Returning xochitl to stock (disable XOVI hooking)..."
ssh "$RM" "test -x /home/root/xovi/stock && /home/root/xovi/stock || true"

echo "==> 3/5 Deleting XOVI files from the data partition..."
ssh "$RM" "rm -rf /home/root/xovi /home/root/xovi-tripletap /home/root/.local/share/vellum 2>/dev/null; true"

echo "==> 4/5 Verifying the ROOT partition is clean..."
LEFTOVER=$(ssh "$RM" "ls /etc/systemd/system 2>/dev/null | grep -i -E 'xovi|tripletap' ; systemctl list-unit-files 2>/dev/null | grep -i -E 'xovi|tripletap'" || true)
if [ -n "$LEFTOVER" ]; then
  echo "!!  ROOT PARTITION NOT CLEAN - do NOT disable dev mode yet:"
  echo "$LEFTOVER"
  echo "    Remove manually: mount -o remount,rw / && rm /etc/systemd/system/xovi-tripletap.service && systemctl daemon-reload"
  exit 1
fi
echo "    clean."

echo "==> 5/5 Reboot check..."
ssh "$RM" "reboot" || true

cat <<'EOF'

Root partition is clean. Final manual steps:
  1. Wait for reboot; confirm the device boots to a stock-looking xochitl
     (no XOVI features, triple-tap does nothing).
  2. Confirm cloud sync is green (your notes are in the cloud).
  3. Disable developer mode using the reMarkable Recovery application
     (https://support.remarkable.com/s/article/Software-recovery).
     This wipes the data partition and restores the locked stock state.
  4. After it comes back up, verify: no developer-mode boot warning,
     onboarding is stock. Then ship it.
EOF
