#!/usr/bin/env bash
# Daily crawl entry point (safe to run from cron). Uses flock so overlapping runs are skipped.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${JOBS_LOG_DIR:-$ROOT/data/logs}"
LOG="$LOG_DIR/crawl.log"
PY="$ROOT/.venv/bin/python"

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

{
  echo "=== $(date -Is) crawl start ==="
  if "$PY" -m app.cli crawl --quiet; then
    echo "=== $(date -Is) crawl ok ==="
  else
    code=$?
    echo "=== $(date -Is) crawl FAILED (exit $code) ==="
    exit "$code"
  fi
} >> "$LOG" 2>&1
