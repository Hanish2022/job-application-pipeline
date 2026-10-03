"""The "feed": a profile-less crawl done by GitHub Actions, imported here with *your* profile.

Flow:  Actions crawl -> feed.db (public job postings only, no personal data) -> force-pushed to the `feed` branch of a
PRIVATE repo -> `sync-feed` fetches it over git/SSH -> jobs are filtered + scored with your resume and merged into
your local database. Your Save / Applied / Dismissed marks are never touched by an import.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config
from .models import Job
from .pipeline import PipelineBusy, ensure_profile, is_relevant, rescore, run_pipeline
from .store import Store

BRANCH = "feed"
FILE = "feed.db"
STALE_HOURS = 72
# Only GitHub remotes, in the two usual shapes. Rejects things like ext::, file://, ssh://evil, or option-looking strings.
_URL_RE = re.compile(r"^(?:git@github\.com:|https://github\.com/)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SELECT = (
    "SELECT fingerprint, source, external_id, board, title, company, location, remote, url, description, posted_at, "
    "salary, salary_min_lpa, salary_max_lpa, salary_currency, department, employment_type, tags, closed FROM jobs"
)


class FeedError(Exception):
    pass


def validate_url(url: str) -> str:
    url = (url or "").strip()
    if not _URL_RE.match(url):
        raise FeedError(
            "FEED_REPO_URL must look like git@github.com:<user>/<repo>.git or https://github.com/<user>/<repo>.git"
        )
    return url


# --------------------------------------------------------------------------- fetching
def _git(args: list[str], cwd: Path | None = None, timeout: int = 180) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_SSH_COMMAND=os.environ.get("GIT_SSH_COMMAND", "ssh -o BatchMode=yes"))
    try:
        res = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise FeedError(f"git failed: {e}") from e
    if res.returncode != 0:
        lines = [l.strip() for l in (res.stderr or res.stdout).splitlines() if l.strip()]
        key = next((l for l in lines if l.startswith(("ERROR:", "fatal:", "error:"))), lines[-1] if lines else "git failed")
        raise FeedError(re.sub(r"^(ERROR|fatal|error):\s*", "", key))
    return res.stdout


def fetch_feed(url: str, cache_dir: Path | None = None, _validate: bool = True) -> Path:
    """Download the latest feed.db (single commit on the `feed` branch) into a local cache; returns its path."""
    if _validate:
        url = validate_url(url)
    cache = Path(cache_dir or config.DATA_DIR / "feed-cache")
    cache.mkdir(parents=True, exist_ok=True)
    if not (cache / ".git").exists():
        _git(["init", "-q"], cwd=cache)
    have = _git(["remote"], cwd=cache).split()
    _git(["remote", "set-url", "origin", url] if "origin" in have else ["remote", "add", "origin", url], cwd=cache)
    try:
        _git(["fetch", "-q", "--depth", "1", "--force", "origin", f"{BRANCH}:refs/remotes/origin/{BRANCH}"], cwd=cache)
    except FeedError as e:
        raise FeedError(
            f"could not fetch the '{BRANCH}' branch ({e}). Has the GitHub Action run yet, and does your SSH key "
            f"have access to that repo?"
        ) from e
    _git(["checkout", "-q", "-f", "--detach", f"origin/{BRANCH}"], cwd=cache)
    path = cache / FILE
    if not path.exists():
        raise FeedError(f"the '{BRANCH}' branch has no {FILE}")
    return path


# ---------------------------------------------------------------------------- reading
def read_feed(path: Path) -> tuple[list[Job], list[str], dict]:
    """(open jobs, fingerprints the feed marks closed, meta)."""
    try:
        con = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(_SELECT).fetchall()
        meta = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM kv WHERE key != 'profile'")}
        con.close()
    except sqlite3.Error as e:
        raise FeedError(f"not a valid feed database: {e}") from e
    jobs: list[Job] = []
    closed: list[str] = []
    for r in rows:
        if r["closed"]:
            closed.append(r["fingerprint"])
            continue
        jobs.append(Job(
            source=r["source"], external_id=r["external_id"], title=r["title"], company=r["company"], url=r["url"],
            location=r["location"], remote=bool(r["remote"]), description=r["description"], posted_at=r["posted_at"],
            salary=r["salary"], department=r["department"], employment_type=r["employment_type"], board=r["board"],
            tags=json.loads(r["tags"] or "[]"), salary_lpa_min=r["salary_min_lpa"], salary_lpa_max=r["salary_max_lpa"],
            salary_currency=r["salary_currency"],
        ))
    return jobs, closed, meta


def feed_age_hours(meta: dict, now: Optional[datetime] = None) -> Optional[float]:
    raw = meta.get("feed_generated_at")
    if not raw:
        return None
    try:
        return ((now or datetime.now(timezone.utc)) - datetime.fromisoformat(raw)).total_seconds() / 3600
    except ValueError:
        return None


# --------------------------------------------------------------------------- importing
def import_feed(store: Store, path: Path, profile: dict) -> dict:
    jobs, closed_fps, meta = read_feed(path)
    relevant = [j for j in jobs if is_relevant(j, profile)]
    res = store.upsert_jobs(relevant)
    closed = store.close_fingerprints(closed_fps)
    closed += store.close_unseen(["wellfound", "yc"], 21)
    age = feed_age_hours(meta)
    return {
        "read": len(jobs), "kept": len(relevant), "skipped_irrelevant": len(jobs) - len(relevant),
        "inserted": res["inserted"], "updated": res["updated"], "closed": closed,
        "feed_generated_at": meta.get("feed_generated_at"), "feed_age_hours": None if age is None else round(age, 1),
        "feed_stale": age is not None and age > STALE_HOURS,
    }


def run_sync(store: Store, url: str | None = None, path: Path | None = None, resume=None) -> dict:
    """Fetch (or use `path`) and import, recording a run so `due`, the dashboard and cron treat it like a crawl."""
    if store.running_run():
        raise PipelineBusy("A refresh is already running")
    run_id = store.start_run()
    try:
        profile = ensure_profile(store, resume)
        feed_path = Path(path) if path else fetch_feed(url or config.FEED_REPO_URL)
        info = import_feed(store, feed_path, profile)
        stats = {"sources": {"feed": info}, "errors": [], "rescored": rescore(store, profile)}
        if info["feed_stale"]:
            stats["errors"].append(
                f"feed is {info['feed_age_hours']}h old - is the GitHub Action still running? (Actions tab -> daily-job-feed)"
            )
        store.finish_run(run_id, "ok", stats)
        return stats
    except Exception as e:  # noqa: BLE001
        store.finish_run(run_id, "failed", {"error": f"{type(e).__name__}: {e}"})
        raise


def refresh(store: Store, resume=None) -> dict:
    """What "Refresh jobs" and cron do: pull the GitHub feed if one is configured, otherwise crawl locally."""
    if config.FEED_REPO_URL:
        try:
            return run_sync(store, resume=resume)
        except FeedError as e:
            # Feed not published yet / repo unreachable / no network: still give the user fresh jobs.
            stats = run_pipeline(store, resume=resume)
            stats.setdefault("errors", []).append(f"GitHub feed unavailable ({e}); crawled locally instead")
            return stats
    return run_pipeline(store, resume=resume)
