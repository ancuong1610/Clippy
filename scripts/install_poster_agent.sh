#!/bin/zsh
# Install (or reinstall) the Clippy poster LaunchAgent in the GUI session. Modelled on
# BabeBasketAutomation/scripts/install_check_agent.sh.
#   scripts/install_poster_agent.sh          install / reload
#   scripts/install_poster_agent.sh remove   uninstall
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=com.clippy.poster
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "${1:-}" = remove ]; then rm -f "$DEST"; echo "removed $LABEL"; exit 0; fi
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
sed -e "s|__ROOT__|$ROOT|g" -e "s|__HOME__|$HOME|g" "$ROOT/scripts/$LABEL.plist" > "$DEST"
plutil -lint "$DEST"
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "installed $LABEL -> $DEST"
