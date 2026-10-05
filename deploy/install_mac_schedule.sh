#!/bin/sh
# Installs the launchd job that calls `trader tick` every 5 minutes (runs only while the Mac is awake and logged in).
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$REPO/data"
DEST="$HOME/Library/LaunchAgents/com.ai-trader.tick.plist"
sed "s#__REPO__#$REPO#g" "$REPO/deploy/com.ai-trader.tick.plist" > "$DEST"
launchctl unload "$DEST" 2>/dev/null || true
launchctl load "$DEST"
echo "Installed. Log: $REPO/data/tick.log   Remove: launchctl unload $DEST && rm $DEST"
