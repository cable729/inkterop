#!/bin/bash
# Install the inkterop watch daemon as a per-user launchd agent.
set -euo pipefail
CORE="$(cd "$(dirname "$0")/.." && pwd)"
UV="$(command -v uv)"
PLIST=~/Library/LaunchAgents/com.inkterop.watch.plist
mkdir -p ~/Library/LaunchAgents ~/.config/inkterop
sed -e "s|__UV__|$UV|g" -e "s|__CORE__|$CORE|g" -e "s|__HOME__|$HOME|g" \
    "$CORE/launchd/com.inkterop.watch.plist" > "$PLIST"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Loaded. Log: ~/.config/inkterop/watch.log"
echo "Note: the reMarkable desktop app must be running for the cache to sync."
