#!/usr/bin/env bash
# One-time setup: virtualenv, pinned dependencies, Playwright's Chromium (for the tests).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -d .venv ]] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
.venv/bin/playwright install chromium
echo "Setup complete. Next: ./scripts/run.sh"
