"""Ingestion pipeline: fetch -> filter -> normalise -> dedupe/store -> score."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import httpx

from . import config
from .matcher import _TECH_TITLE, score_job
from .models import Job
from .resume import ResumeError, profile_from_file
from .sources import adzuna, arbeitnow, ashby, greenhouse, himalayas, lever, remotive, tinyfish
from .sources.http import make_client
from .store import Store, fingerprint
from .textutil import is_india, remote_eligible

log = logging.getLogger("jobs.pipeline")

FETCHERS = {"greenhouse": greenhouse.fetch, "lever": lever.fetch, "ashby": ashby.fetch}


class PipelineBusy(Exception):
    pass


# Used by the GitHub Actions feed crawl: no resume, no personal data - just "engineering jobs I could take from India".
GENERIC_PROFILE = {"locations": ["india", "remote"]}


def load_companies(path: Path | None = None) -> dict:
    return json.loads(Path(path or config.COMPANIES_PATH).read_text())


def is_relevant(job: Job, profile: dict | None) -> bool:
    """Cheap pre-filter so we only store engineering roles in locations the user can take."""
    if not _TECH_TITLE.search(job.title):
        return False
    prefs = set((profile or {}).get("locations") or [])
    if not prefs:
        return True
    loc = job.location or ""
    if "india" in prefs and is_india(loc):
        return True
    if "remote" in prefs and remote_eligible(loc, job.remote):
        return True
    return False


def ensure_profile(store: Store, resume: str | Path | None = None, force: bool = False) -> dict:
    """Return the saved profile, (re)building it from the resume when missing or forced."""
    profile = None if force else store.load_profile()
    if profile is None:
        path = Path(resume or config.DEFAULT_RESUME)
        profile = profile_from_file(path)
        store.save_profile(profile)
    return profile


def rescore(store: Store, profile: dict) -> int:
    rows = []
    for r in store.all_for_scoring():
        s = score_job(
            r["title"], r["description"], is_india=bool(r["is_india"]), remote=bool(r["remote"]),
            profile=profile, tags=r["tags"], department=r["department"],
        )
        rows.append((r["id"], s.score, s.level, s.matched, s.reasons))
    return store.update_scores(rows)


def _apply_names(jobs: list[Job], names: dict) -> None:
    for j in jobs:
        pretty = names.get(j.board)
        if pretty and j.source in ("lever", "ashby"):
            j.company = pretty


async def crawl(
    store: Store,
    companies: dict,
    profile: dict | None,
    client: Optional[httpx.AsyncClient] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> dict:
    """Fetch every configured source concurrently. One failing source never aborts the run."""
    own_client = client is None
    client = client or make_client()
    sem = asyncio.Semaphore(config.MAX_CONCURRENCY)
    names = companies.get("names", {})
    stats: dict = {"sources": {}, "errors": []}

    tasks: list[tuple[str, str, Callable]] = []
    for src, fetcher in FETCHERS.items():
        for token in companies.get(src, []):
            tasks.append((src, token, lambda f=fetcher, t=token: f(client, t)))

    agg = companies.get("aggregators", {})
    if agg.get("remotive", {}).get("enabled"):
        for cat in agg["remotive"].get("categories", ["software-dev"]):
            tasks.append(("remotive", cat, lambda c=cat: remotive.fetch(client, c)))
    if agg.get("himalayas", {}).get("enabled"):
        h = agg["himalayas"]
        tasks.append(("himalayas", "himalayas", lambda: himalayas.fetch(client, h.get("searches", []), h.get("pages", 3))))
    if agg.get("arbeitnow", {}).get("enabled"):
        tasks.append(("arbeitnow", "arbeitnow", lambda: arbeitnow.fetch(client, agg["arbeitnow"].get("pages", 3))))
    az = agg.get("adzuna", {})
    if az.get("enabled") and adzuna.enabled():
        for query in az.get("queries", []):
            tasks.append(("adzuna", query, lambda q=query: adzuna.fetch(client, az.get("country", "in"), q)))

    async def run_one(src: str, board: str, make: Callable):
        async with sem:
            try:
                jobs: list[Job] = await make()
            except Exception as e:  # noqa: BLE001 - isolate every source
                msg = f"{src}:{board} {type(e).__name__}: {e}"
                log.warning("fetch failed %s", msg)
                stats["errors"].append(msg)
                return
        _apply_names(jobs, names)
        kept = [j for j in jobs if is_relevant(j, profile)]
        res = store.upsert_jobs(kept)
        closed = 0
        # Only aggregator-free, full-board sources can tell us a job disappeared.
        if src in FETCHERS:
            closed = store.close_missing(src, board, {fingerprint(j) for j in kept})
        s = stats["sources"].setdefault(src, {"boards": 0, "fetched": 0, "kept": 0, "inserted": 0, "updated": 0, "closed": 0})
        s["boards"] += 1
        s["fetched"] += len(jobs)
        s["kept"] += len(kept)
        s["inserted"] += res["inserted"]
        s["updated"] += res["updated"]
        s["closed"] += closed
        if on_progress:
            on_progress(f"{src}:{board} fetched={len(jobs)} kept={len(kept)} new={res['inserted']}")

    async def run_tinyfish(cfg: dict):
        """Wellfound + YC discovery. Its own runner: search-driven, capped fetches, 'vanished' closing."""
        info: dict = {}
        async with sem:
            try:
                known = store.known_urls(["wellfound", "yc"], tinyfish.PREVIEW_TAG)
                known.update({u: True for u in store.rejected_urls()})      # recently rejected => don't re-fetch
                jobs = await tinyfish.discover(client, cfg, known, lambda j: is_relevant(j, profile), info)
            except Exception as e:  # noqa: BLE001
                msg = f"tinyfish: {type(e).__name__}: {e}"
                log.warning("discovery failed %s", msg)
                stats["errors"].append(msg)
                return
        for err in info.pop("errors", []):
            stats["errors"].append(f"tinyfish: {err}")
        seen = info.pop("seen_urls", [])
        kept = [j for j in jobs if is_relevant(j, profile)]
        res = store.upsert_jobs(kept)
        kept_urls = {j.url for j in kept}
        store.add_rejected([u for u in info.pop("processed", []) if u not in kept_urls])
        store.touch_urls(seen)                       # still listed => still open
        closed = store.close_unseen(["wellfound", "yc"], int(cfg.get("close_after_days", 21)))
        stats["sources"]["tinyfish"] = {**info, "kept": len(kept), "inserted": res["inserted"],
                                        "updated": res["updated"], "closed": closed}
        if on_progress:
            on_progress(f"tinyfish found={info.get('found')} fetched={info.get('fetched')} new={res['inserted']}")

    tf = agg.get("tinyfish", {})
    tf_task = [run_tinyfish(tf)] if tf.get("enabled") and tinyfish.enabled() else []
    if tf.get("enabled") and not tinyfish.enabled():
        stats["errors"].append("tinyfish: skipped (TINYFISH_API_KEY not set)")

    try:
        await asyncio.gather(*(run_one(*t) for t in tasks), *tf_task)
    finally:
        if own_client:
            await client.aclose()
    stats["sources_attempted"] = len(tasks) + len(tf_task)
    return stats


def run_pipeline(
    store: Store,
    companies_path: Path | None = None,
    resume: str | Path | None = None,
    rebuild_profile: bool = False,
    client: Optional[httpx.AsyncClient] = None,
    on_progress: Optional[Callable[[str], None]] = None,
    generic: bool = False,
) -> dict:
    """Full daily run. Raises PipelineBusy if another run is active.

    generic=True is the "feed" mode used by GitHub Actions: no resume/profile is read or stored and jobs are not scored
    (the consumer scores them with its own profile)."""
    if store.running_run():
        raise PipelineBusy("A crawl is already running")
    run_id = store.start_run()
    try:
        profile = GENERIC_PROFILE if generic else ensure_profile(store, resume, force=rebuild_profile)
        stats = asyncio.run(crawl(store, load_companies(companies_path), profile, client, on_progress))
        stats["rescored"] = 0 if generic else rescore(store, profile)
        if generic:
            store.set_meta("feed_generated_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        failed_all = stats["sources_attempted"] > 0 and not stats["sources"]
        store.finish_run(run_id, "failed" if failed_all else "ok", stats)
        return stats
    except ResumeError as e:
        store.finish_run(run_id, "failed", {"error": str(e)})
        raise
    except Exception as e:  # noqa: BLE001
        store.finish_run(run_id, "failed", {"error": f"{type(e).__name__}: {e}"})
        raise
