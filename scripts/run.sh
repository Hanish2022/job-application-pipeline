#!/usr/bin/env bash
# Start the dashboard on http://127.0.0.1:8000 (local only; the app has no login).
# If the database is empty and a resume is available, runs a first crawl so the dashboard isn't blank.
# PORT / HOST can be overridden:  PORT=9000 ./scripts/run.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY=".venv/bin/python"
[[ -x "$PY" ]] || { echo "Run ./scripts/setup.sh first"; exit 1; }

# empty = ready to crawl; otherwise a short reason why we should not
state="$($PY - <<'EOF'
from app import config
from app.store import Store

s = Store()
if s.stats()["total"] > 0:
    print("has-jobs")
elif s.load_profile() is None and not config.DEFAULT_RESUME.exists():
    print("no-resume")
else:
    print("empty")
EOF
)"

case "$state" in
  empty)
    echo "First run: crawling job boards (about a minute or two)…"
    $PY -m app.cli crawl --quiet || echo "Crawl had problems - the dashboard will still start."
    ;;
  no-resume)
    echo "No resume found yet. Open the dashboard, go to Profile → Upload new resume, then click Refresh jobs."
    echo "(Or put a PDF at ~/Downloads/new_resume.pdf / set JOBS_RESUME in .env and run this again.)"
    ;;
esac

echo "Dashboard: http://${HOST:-127.0.0.1}:${PORT:-8000}"
exec $PY -m app.cli serve --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
