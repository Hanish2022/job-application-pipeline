#!/usr/bin/env bash
# Crawl entry point for cron. Safe to call as often as you like: it only crawls when a run is *due*
# (no successful run since today's slot, default 08:30; set JOBS_RUN_AT=HH:MM to change), so a laptop
# that was off or asleep at the slot still catches up on the next hourly tick or at boot.
# flock makes overlapping invocations skip instead of running twice.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${JOBS_LOG_DIR:-$ROOT/data/logs}"
LOG="$LOG_DIR/crawl.log"
PY="${JOBS_PYTHON:-$ROOT/.venv/bin/python}"   # override is for the test-suite
RUN_AT="${JOBS_RUN_AT:-08:30}"

mkdir -p "$LOG_DIR"
cd "$ROOT"

if [[ ! -x "$PY" ]]; then
  echo "$(date -Is) ERROR: $PY not found - run ./scripts/setup.sh first" >> "$LOG"
  exit 1
fi

# Keep the log from growing forever (rotate at ~1 MB, keep one old copy).
if [[ -f "$LOG" && $(stat -c %s "$LOG") -gt 1048576 ]]; then
  mv "$LOG" "$LOG.1"
fi

exec 9>"$LOG_DIR/crawl.lock"
if ! flock -n 9; then
  echo "$(date -Is) skipped: previous crawl still running" >> "$LOG"
  exit 0
fi

# Exit 1 = not due (stay silent, this runs hourly); anything else non-zero = a real error.
set +e
reason="$("$PY" -m app.cli due --at "$RUN_AT" 2>&1)"
code=$?
set -e
if [[ $code -eq 1 ]]; then
  exit 0
elif [[ $code -ne 0 ]]; then
  echo "$(date -Is) ERROR checking schedule: $reason" >> "$LOG"
  exit "$code"
fi

{
  echo "=== $(date -Is) crawl start ($reason) ==="
  if "$PY" -m app.cli refresh --quiet; then
    echo "=== $(date -Is) crawl ok ==="
  else
    code=$?
    echo "=== $(date -Is) crawl FAILED (exit $code) ==="
    exit "$code"
  fi
} >> "$LOG" 2>&1
