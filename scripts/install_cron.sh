#!/usr/bin/env bash
# Manage the crawl schedule.   ./scripts/install_cron.sh [--print | --install | --remove | --status] [--time HH:MM]
# Default action is --print: nothing touches your crontab unless you pass --install / --remove.
#
# Three crontab lines are installed. All call the same guarded script, which only crawls when a run is due,
# so you get exactly one crawl per day even on a laptop that is often off or asleep:
#   1. daily at HH:MM            - the normal run
#   2. every hour, on the hour   - catch-up if the machine was off/asleep at HH:MM
#   3. @reboot (after 2 min)     - catch-up after a restart
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARK="# jobs-pipeline daily crawl"
ACTION="--print"
TIME="08:30"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --print|--install|--remove|--status) ACTION="$1"; shift ;;
    --time) TIME="${2:?--time needs HH:MM}"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if ! [[ "$TIME" =~ ^([01][0-9]|2[0-3]):([0-5][0-9])$ ]]; then
  echo "invalid --time '$TIME' (use HH:MM, 24h)" >&2; exit 2
fi
HOUR=$((10#${TIME%%:*})); MIN=$((10#${TIME##*:}))
CMD="JOBS_RUN_AT=$TIME \"$ROOT/scripts/cron_crawl.sh\""
LINES="$MIN $HOUR * * * $CMD $MARK
0 * * * * $CMD $MARK
@reboot sleep 120 && $CMD $MARK"

current="$(crontab -l 2>/dev/null || true)"
without="$(printf '%s\n' "$current" | grep -vF "$MARK" || true)"

case "$ACTION" in
  --print)
    printf '%s\n' "$LINES"
    echo "(run with --install to add these to your crontab)"
    ;;
  --install)
    { [[ -n "$without" ]] && printf '%s\n' "$without"; printf '%s\n' "$LINES"; } | crontab -
    echo "Installed (daily at $TIME, with hourly + boot catch-up):"
    printf '%s\n' "$LINES" | sed 's/^/  /'
    ;;
  --remove)
    if [[ -n "$without" ]]; then printf '%s\n' "$without" | crontab -; else crontab -r 2>/dev/null || true; fi
    echo "Removed jobs-pipeline cron entries"
    ;;
  --status)
    ours="$(printf '%s\n' "$current" | grep -F "$MARK" || true)"
    if [[ -z "$ours" ]]; then echo "Not installed."; else echo "Installed:"; printf '%s\n' "$ours" | sed 's/^/  /'; fi
    echo "Last log lines:"; tail -n 4 "$ROOT/data/logs/crawl.log" 2>/dev/null | sed 's/^/  /' || true
    ;;
esac
