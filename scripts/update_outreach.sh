#!/usr/bin/env bash
# Vendor / update the third-party yc-outreach project (MIT) into vendor/yc-outreach, unmodified.
#
#   scripts/update_outreach.sh            review what upstream changed (nothing is written)
#   scripts/update_outreach.sh --apply    copy it in, refresh vendor/yc-outreach/UPSTREAM.json (then run the tests!)
#
# UPSTREAM_URL / UPSTREAM_REF override the source (default: adityajha2005/yc-outreach @ main).
# The vendored files are never edited by hand: our integration lives in app/outreach.py and app/static/, so updating is a plain copy.
# ALWAYS read the diff before --apply: this code runs on your machine, same-origin with your dashboard.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/vendor/yc-outreach"
URL="${UPSTREAM_URL:-https://github.com/adityajha2005/yc-outreach.git}"
REF="${UPSTREAM_REF:-main}"
PY="${JOBS_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY=python3
APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1
[[ $# -le 1 && ( -z "${1:-}" || "${1:-}" == "--apply" ) ]] || { echo "usage: $0 [--apply]" >&2; exit 2; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
git clone -q --depth 1 --branch "$REF" "$URL" "$tmp/src"
commit="$(git -C "$tmp/src" rev-parse HEAD)"
rm -rf "$tmp/src/.git"

old_commit="(none)"
[[ -f "$DEST/UPSTREAM.json" ]] && old_commit="$("$PY" -c 'import json,sys;print(json.load(open(sys.argv[1]))["commit"])' "$DEST/UPSTREAM.json")"
echo "upstream: $URL @ $REF"
echo "vendored: $old_commit"
echo "latest:   $commit"

if [[ -d "$DEST" ]]; then
  if diff -rq --exclude=UPSTREAM.json "$DEST" "$tmp/src" >"$tmp/brief.txt"; then
    echo "No file changes."
  else
    echo "--- changed files:"; sed 's/^/  /' "$tmp/brief.txt"
    echo "--- diff (first 200 lines):"; diff -ru --exclude=UPSTREAM.json "$DEST" "$tmp/src" | head -200 || true
  fi
fi

if [[ $APPLY -eq 0 ]]; then
  echo; echo "Nothing written. Review the diff above, then run:  $0 --apply"
  exit 0
fi

mkdir -p "$DEST"
find "$DEST" -mindepth 1 -not -name UPSTREAM.json -delete
tar -C "$tmp/src" -cf - . | tar -C "$DEST" -xf -
"$PY" - "$DEST" "$URL" "$REF" "$commit" <<'EOF'
import hashlib, json, pathlib, sys, datetime
dest, url, ref, commit = pathlib.Path(sys.argv[1]), *sys.argv[2:5]
files = {str(p.relative_to(dest)): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in sorted(dest.rglob("*")) if p.is_file() and p.name != "UPSTREAM.json"}
(dest / "UPSTREAM.json").write_text(json.dumps({
    "project": "yc-outreach", "url": url.removesuffix(".git"), "ref": ref, "commit": commit, "license": "MIT",
    "vendored_on": datetime.date.today().isoformat(),
    "note": "Unmodified copy. Our integration is in app/outreach.py and app/static/. Update with scripts/update_outreach.sh.",
    "files": files}, indent=2) + "\n")
print(f"vendored {len(files)} files at {commit[:7]}")
EOF
