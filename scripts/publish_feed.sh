#!/usr/bin/env bash
# Publish the feed database as ONE orphan commit on the `feed` branch of $FEED_REMOTE (force-push, so the repo never grows).
# Used by the GitHub Action; also handy to run by hand.   usage: FEED_REMOTE=git@github.com:me/private-repo.git scripts/publish_feed.sh [data/feed.db]
set -euo pipefail

: "${FEED_REMOTE:?set FEED_REMOTE to the (private!) repo that receives the feed}"
DB="${1:-data/feed.db}"
[[ -f "$DB" ]] || { echo "publish_feed: $DB not found" >&2; exit 1; }

PY="${JOBS_PYTHON:-python3}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# Fold the WAL into the main file and compact it, then make sure no personal profile can ride along.
"$PY" - "$DB" "$tmp/feed.db" <<'EOF'
import shutil, sqlite3, sys
src, dst = sys.argv[1:3]
con = sqlite3.connect(src)
con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
con.execute("DELETE FROM kv WHERE key = 'profile'")       # a feed must never contain a profile
con.commit()
con.execute("VACUUM")
con.close()
shutil.copyfile(src, dst)
EOF

cd "$tmp"
git init -q -b feed
git config user.name "jobs-feed-bot"
git config user.email "jobs-feed-bot@users.noreply.github.com"
git add feed.db
git commit -q -m "feed $(date -u +%Y-%m-%dT%H:%MZ)"
git push -q --force "$FEED_REMOTE" feed:feed
echo "publish_feed: pushed $(du -h feed.db | cut -f1) to branch 'feed'"
