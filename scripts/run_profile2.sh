#!/usr/bin/env bash
# Second, completely separate copy of the dashboard (own database, own resume, own statuses).
#   http://127.0.0.1:8002   data in data-profile2/
# It starts EMPTY: open Profile -> Upload new resume, then click Refresh jobs.
# It is NOT part of the daily cron job (cron only refreshes the main copy in data/).
# Override with:  PORT=8003 PROFILE_DIR=data-profile3 ./scripts/run_profile2.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export JOBS_DATA_DIR="$ROOT/${PROFILE_DIR:-data-profile2}"
unset JOBS_DB_PATH
# never auto-load a default resume or auto-crawl for this copy
export JOBS_RESUME="$JOBS_DATA_DIR/.no-default-resume"
mkdir -p "$JOBS_DATA_DIR"
chmod 700 "$JOBS_DATA_DIR"
export PORT="${PORT:-8002}"
exec "$ROOT/scripts/run.sh"
