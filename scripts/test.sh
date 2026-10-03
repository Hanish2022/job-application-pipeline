#!/usr/bin/env bash
# Run the whole suite (unit tests first, then the Playwright browser tests). Extra args go to pytest.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -x .venv/bin/python ]] || { echo "Run ./scripts/setup.sh first"; exit 1; }
exec .venv/bin/python -m pytest -p no:cacheprovider "$@"
