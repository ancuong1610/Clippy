#!/bin/zsh
# launchd entry for com.clippy.poster. `clipfarm run` (live watcher) has its own in-process poster and there is
# no cross-process lock, so never run two posters: if one is already up, wait and let launchd retry later.
set -eu
cd "$(dirname "$0")/.."
if pgrep -f 'clipfarm (run|post --watch)' >/dev/null; then
  echo "$(date '+%F %T') another clipfarm poster is running; not starting a second one"
  sleep 300
  exit 0
fi
exec /opt/homebrew/bin/uv run clipfarm post --watch
