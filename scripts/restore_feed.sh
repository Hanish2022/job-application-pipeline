#!/usr/bin/env bash
# Restore the previous feed.db (if any) so the crawl remembers what it already fetched/rejected and can close vanished jobs.
# A missing `feed` branch (first ever run) is fine: it just starts fresh.   usage: FEED_REMOTE=... scripts/restore_feed.sh [data/feed.db]
set -euo pipefail

: "${FEED_REMOTE:?set FEED_REMOTE}"
DB="${1:-data/feed.db}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$(dirname "$DB")"

if git clone -q --depth 1 --branch feed --single-branch "$FEED_REMOTE" "$tmp/repo" 2>"$tmp/err"; then
  if [[ -f "$tmp/repo/feed.db" ]]; then
    cp "$tmp/repo/feed.db" "$DB"
    echo "restore_feed: restored $(du -h "$DB" | cut -f1) from the feed branch"
  else
    echo "restore_feed: feed branch has no feed.db - starting fresh"
  fi
elif grep -qiE "remote branch feed not found|couldn't find remote ref" "$tmp/err"; then
  echo "restore_feed: no feed branch yet (first run) - starting fresh"
else
  echo "restore_feed: could not read $FEED_REMOTE:" >&2
  cat "$tmp/err" >&2
  exit 1          # a real problem (bad key, wrong repo): fail loudly rather than silently losing history
fi
