#!/usr/bin/env bash
# Manage the daily cron entry.   ./scripts/install_cron.sh [--print | --install | --remove] [--time "HH:MM"]
# Default: print the line only. Nothing touches your crontab unless you pass --install / --remove.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARK="# jobs-pipeline daily crawl"
ACTION="--print"
TIME="08:30"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --print|--install|--remove) ACTION="$1"; shift ;;
    --time) TIME="${2:?--time needs HH:MM}"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if ! [[ "$TIME" =~ ^([01][0-9]|2[0-3]):([0-5][0-9])$ ]]; then
  echo "invalid --time '$TIME' (use HH:MM, 24h)" >&2; exit 2
fi
HOUR=$((10#${TIME%%:*})); MIN=$((10#${TIME##*:}))
LINE="$MIN $HOUR * * * \"$ROOT/scripts/cron_crawl.sh\" $MARK"

current="$(crontab -l 2>/dev/null || true)"
without="$(printf '%s\n' "$current" | grep -vF "$MARK" || true)"

case "$ACTION" in
  --print)
    echo "$LINE"
    echo "(run with --install to add it to your crontab)"
    ;;
  --install)
    { [[ -n "$without" ]] && printf '%s\n' "$without"; printf '%s\n' "$LINE"; } | crontab -
    echo "Installed: $LINE"
    ;;
  --remove)
    if [[ -n "$without" ]]; then printf '%s\n' "$without" | crontab -; else crontab -r 2>/dev/null || true; fi
    echo "Removed jobs-pipeline cron entry"
    ;;
esac
