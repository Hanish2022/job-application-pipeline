"""The GitHub-Actions feed: profile-less crawl -> feed.db -> private git repo -> import with your own profile."""
import asyncio
import os
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx
import yaml

from app import config, feed
from app.feed import FeedError, fetch_feed, import_feed, read_feed, refresh, run_sync, validate_url
from app.pipeline import GENERIC_PROFILE, PipelineBusy, run_pipeline
from app.store import Store
from tests.conftest import RESUME_TEXT, make_job

ROOT = Path(__file__).resolve().parent.parent
PUBLISH = ROOT / "scripts" / "publish_feed.sh"
RESTORE = ROOT / "scripts" / "restore_feed.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "daily-job-feed.yml"


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, **kw)


def make_feed(tmp_path, jobs, closed_titles=(), generated=None) -> Path:
    """Build a feed.db the way the Actions crawl would."""
    path = tmp_path / "feed.db"
    s = Store(path)
    s.upsert_jobs(jobs)
    for title in closed_titles:
        with s.conn() as c:
            c.execute("UPDATE jobs SET closed=1 WHERE title=?", (title,))
    s.set_meta("feed_generated_at", (generated or datetime.now(timezone.utc)).isoformat(timespec="seconds"))
    return path


@pytest.fixture()
def local(store, profile):
    store.save_profile(profile)
    return store


# ------------------------------------------------------------------ URL validation
@pytest.mark.parametrize("url", [
    "git@github.com:Hanish2022/job-application-pipeline-data.git", "git@github.com:me/repo",
    "https://github.com/me/repo.git", "https://github.com/me/repo",
])
def test_valid_feed_urls(url):
    assert validate_url(url) == url


@pytest.mark.parametrize("url", [
    "", "file:///etc", "/tmp/repo", "ext::sh -c 'id'", "ssh://evil.com/x/y.git", "git@evil.com:me/repo.git",
    "https://evil.com/me/repo.git", "-oProxyCommand=id", "git@github.com:me/repo.git; rm -rf /", "https://github.com/me/repo/../x",
    "git@github.com:me/repo name.git",
])
def test_invalid_feed_urls_rejected(url):
    with pytest.raises(FeedError):
        validate_url(url)


# ------------------------------------------------------------------ read + import
def test_read_feed_separates_open_and_closed_and_reads_meta(tmp_path):
    path = make_feed(tmp_path, [make_job(external_id="1", title="Open One", salary="₹12-18 LPA"),
                                make_job(external_id="2", title="Gone")], closed_titles=["Gone"])
    jobs, closed, meta = read_feed(path)
    assert [j.title for j in jobs] == ["Open One"] and len(closed) == 1 and "feed_generated_at" in meta
    assert (jobs[0].salary_lpa_min, jobs[0].salary_lpa_max) == (12, 18)


def test_read_feed_rejects_garbage(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a database at all")
    with pytest.raises(FeedError):
        read_feed(bad)
    with pytest.raises(FeedError):
        read_feed(tmp_path / "missing.db")


def test_import_filters_with_local_profile_and_scores(local, tmp_path, profile):
    path = make_feed(tmp_path, [
        make_job(external_id="1", title="Full Stack Developer", description="React Node Express MongoDB", location="Pune, India"),
        make_job(external_id="2", title="Backend Engineer", location="Austin, TX"),              # not take-able from India
        make_job(external_id="3", title="Office Manager", location="Pune, India"),               # not engineering
    ])
    info = import_feed(local, path, profile)
    assert (info["read"], info["kept"], info["inserted"], info["skipped_irrelevant"]) == (3, 1, 1, 2)
    assert {j["title"] for j in local.query_jobs(limit=10)["items"]} == {"Full Stack Developer"}


def test_import_preserves_user_status_and_is_idempotent(local, tmp_path, profile):
    path = make_feed(tmp_path, [make_job(external_id="1", title="Full Stack Developer", description="React Node")])
    import_feed(local, path, profile)
    (job,) = local.query_jobs()["items"]
    local.set_status(job["id"], "applied")
    again = import_feed(local, path, profile)
    assert again["inserted"] == 0 and again["updated"] == 1
    assert local.get_job(job["id"])["status"] == "applied"


def test_import_applies_closures_and_keeps_lpa_lossless(local, tmp_path, profile):
    first = make_feed(tmp_path, [make_job(external_id="1", title="Full Stack Developer", salary_min=60_000, salary_max=80_000, salary_currency="USD"),
                                 make_job(external_id="2", title="Backend Developer")])
    import_feed(local, first, profile)
    by = {j["title"]: j for j in local.query_jobs(limit=10)["items"]}
    assert by["Full Stack Developer"]["salary_lpa"] == "≈ ₹52.8–70.4 LPA" and by["Full Stack Developer"]["salary_converted"]
    (tmp_path / "second").mkdir()
    second = make_feed(tmp_path / "second", [make_job(external_id="1", title="Full Stack Developer"), make_job(external_id="2", title="Backend Developer")],
                       closed_titles=["Backend Developer"])
    info = import_feed(local, second, profile)
    assert info["closed"] >= 1 and {j["title"] for j in local.query_jobs(limit=10)["items"]} == {"Full Stack Developer"}


def test_stale_feed_is_flagged(local, tmp_path, profile):
    old = datetime.now(timezone.utc) - timedelta(hours=100)
    info = import_feed(local, make_feed(tmp_path, [make_job()], generated=old), profile)
    assert info["feed_stale"] is True and 99 < info["feed_age_hours"] < 101
    fresh = import_feed(local, make_feed(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path, [make_job()]), profile)
    assert fresh["feed_stale"] is False


def test_run_sync_records_runs_and_warns_when_stale(local, tmp_path):
    old = datetime.now(timezone.utc) - timedelta(hours=100)
    stats = run_sync(local, path=make_feed(tmp_path, [make_job(title="Software Engineer")], generated=old))
    assert stats["sources"]["feed"]["inserted"] == 1 and any("Action still running" in e for e in stats["errors"])
    assert local.recent_runs(1)[0]["status"] == "ok" and local.last_ok_finished_at()


def test_run_sync_failure_is_recorded_and_busy_is_refused(local, tmp_path):
    with pytest.raises(FeedError):
        run_sync(local, path=tmp_path / "missing.db")
    assert local.recent_runs(1)[0]["status"] == "failed" and local.running_run() is None
    local.start_run()
    with pytest.raises(PipelineBusy):
        run_sync(local, path=tmp_path / "x.db")


def test_refresh_dispatches_on_feed_configuration(store, monkeypatch):
    calls = []
    monkeypatch.setattr(feed, "run_sync", lambda s, **kw: calls.append("sync") or {})
    monkeypatch.setattr(feed, "run_pipeline", lambda s, **kw: calls.append("crawl") or {})
    monkeypatch.setattr(config, "FEED_REPO_URL", "")
    refresh(store)
    monkeypatch.setattr(config, "FEED_REPO_URL", "git@github.com:me/data.git")
    refresh(store)
    assert calls == ["crawl", "sync"]


# ------------------------------------------------------------------ git transport (real git, local bare repo)
@pytest.fixture()
def bare(tmp_path):
    repo = tmp_path / "data-repo.git"
    sh(["git", "init", "-q", "--bare", str(repo)], check=True)
    return repo


def publish(db: Path, remote: Path, **env):
    return sh(["bash", str(PUBLISH), str(db)], env=dict(os.environ, FEED_REMOTE=str(remote), JOBS_PYTHON="python3", **env))


def test_publish_then_fetch_round_trip_and_force_replaces_history(tmp_path, bare):
    db1 = make_feed(tmp_path, [make_job(external_id="1", title="First Developer")])
    r = publish(db1, bare)
    assert r.returncode == 0 and "pushed" in r.stdout, r.stderr
    cache = tmp_path / "cache"
    jobs, _, _ = read_feed(fetch_feed(str(bare), cache, _validate=False))
    assert [j.title for j in jobs] == ["First Developer"]

    (tmp_path / "day2").mkdir()
    db2 = make_feed(tmp_path / "day2", [make_job(external_id="2", title="Second Developer")])
    assert publish(db2, bare).returncode == 0
    jobs, _, _ = read_feed(fetch_feed(str(bare), cache, _validate=False))               # fetching again picks up the new orphan commit
    assert [j.title for j in jobs] == ["Second Developer"]
    count = sh(["git", "--git-dir", str(bare), "rev-list", "--count", "feed"]).stdout.strip()
    assert count == "1"                                                                  # history never grows


def test_published_feed_never_contains_a_profile(tmp_path, bare, profile):
    db = make_feed(tmp_path, [make_job()])
    s = Store(db)
    s.save_profile(profile)                                                              # even if one sneaks in...
    assert publish(db, bare).returncode == 0
    path = fetch_feed(str(bare), tmp_path / "cache", _validate=False)
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM kv WHERE key='profile'").fetchone()[0] == 0   # ...it is stripped on publish
    assert b"Jane Doe" not in path.read_bytes()


def test_fetch_errors_are_helpful(tmp_path, bare):
    with pytest.raises(FeedError, match="Has the GitHub Action run yet"):
        fetch_feed(str(bare), tmp_path / "cache", _validate=False)                        # empty repo: no `feed` branch
    with pytest.raises(FeedError):
        fetch_feed("https://example.com/x/y.git", tmp_path / "cache2")                    # non-GitHub URL refused before any git call


def test_restore_script_handles_first_run_existing_and_broken_remote(tmp_path, bare):
    out = tmp_path / "restored" / "feed.db"
    env = dict(os.environ, FEED_REMOTE=str(bare))
    r = sh(["bash", str(RESTORE), str(out)], env=env)
    assert r.returncode == 0 and "first run" in r.stdout and not out.exists()
    assert publish(make_feed(tmp_path, [make_job(title="Software Engineer")]), bare).returncode == 0
    r = sh(["bash", str(RESTORE), str(out)], env=env)
    assert r.returncode == 0 and out.exists() and "restored" in r.stdout
    r = sh(["bash", str(RESTORE), str(out)], env=dict(os.environ, FEED_REMOTE=str(tmp_path / "does-not-exist.git")))
    assert r.returncode != 0 and "could not read" in r.stderr                             # a real failure must be loud


def test_publish_requires_remote_and_db(tmp_path):
    assert sh(["bash", str(PUBLISH), str(tmp_path / "none.db")], env={k: v for k, v in os.environ.items() if k != "FEED_REMOTE"}).returncode != 0
    r = sh(["bash", str(PUBLISH), str(tmp_path / "none.db")], env=dict(os.environ, FEED_REMOTE="x"))
    assert r.returncode == 1 and "not found" in r.stderr


# ------------------------------------------------------------------ the profile-less crawl
@respx.mock
def test_generic_crawl_needs_no_resume_and_stores_no_profile(store, tmp_path):
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(return_value=httpx.Response(200, json={"jobs": [
        {"id": 1, "title": "Software Engineer", "absolute_url": "https://gh/1", "company_name": "Acme", "location": {"name": "Pune, India"},
         "content": "React", "first_published": "2026-10-01T00:00:00Z"},
        {"id": 2, "title": "Software Engineer", "absolute_url": "https://gh/2", "company_name": "Acme", "location": {"name": "Austin, TX"}},
    ]}))
    cfg = tmp_path / "c.json"
    cfg.write_text('{"greenhouse": ["acme"], "lever": [], "ashby": [], "aggregators": {}}')
    stats = run_pipeline(store, companies_path=cfg, resume=tmp_path / "no-such-resume.pdf", generic=True)
    assert stats["rescored"] == 0 and store.load_profile() is None                        # the missing resume is irrelevant
    (job,) = store.query_jobs(min_score=0)["items"]
    assert job["location"] == "Pune, India" and job["score"] == 0
    assert store.get_meta("feed_generated_at") and store.recent_runs(1)[0]["status"] == "ok"
    assert GENERIC_PROFILE == {"locations": ["india", "remote"]}


# ------------------------------------------------------------------ the workflow file itself
@pytest.fixture(scope="module")
def wf():
    return yaml.safe_load(WORKFLOW.read_text())


def test_workflow_triggers_are_safe(wf):
    on = wf.get(True, wf.get("on"))                                                       # YAML parses the key `on` as boolean True
    assert set(on) == {"schedule", "workflow_dispatch"}                                   # no pull_request(_target): forks can't reach secrets
    assert on["schedule"][0]["cron"] and wf["permissions"] == {"contents": "read"}
    assert wf["concurrency"]["cancel-in-progress"] is False


def test_workflow_uses_secrets_only_in_env_and_never_echoes_them(wf):
    text = WORKFLOW.read_text()
    assert "secrets.FEED_DEPLOY_KEY" in text and "secrets.TINYFISH_API_KEY" in text
    for step in wf["jobs"]["crawl"]["steps"]:
        assert "secrets." not in step.get("run", ""), f"secret interpolated straight into a shell script in step {step.get('name')}"
    for line in text.splitlines():                       # mentioning a secret's NAME is fine; printing its VALUE is not
        if "echo" in line:
            assert "$FEED_DEPLOY_KEY" not in line and "${FEED_DEPLOY_KEY}" not in line and "$TINYFISH_API_KEY" not in line, line
    code = "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))      # ignore comments
    assert "pull_request_target" not in code and "continue-on-error" not in code


def test_workflow_actions_are_first_party_and_version_pinned(wf):
    uses = [s["uses"] for s in wf["jobs"]["crawl"]["steps"] if "uses" in s]
    assert uses and all(u.startswith("actions/") and "@v" in u for u in uses)
    assert wf["jobs"]["crawl"]["timeout-minutes"] <= 30


def test_workflow_runs_the_generic_crawl_then_publishes_and_cleans_up(wf):
    runs = [s.get("run", "") for s in wf["jobs"]["crawl"]["steps"]]
    joined = "\n".join(runs)
    assert joined.index("restore_feed.sh") < joined.index("crawl --generic") < joined.index("publish_feed.sh")
    assert wf["jobs"]["crawl"]["steps"][-1]["if"] == "always()" and "rm -f ~/.ssh/feed_key" in runs[-1]
    assert wf["jobs"]["crawl"]["env"]["JOBS_DB_PATH"] == "data/feed.db"


def test_requirements_split_keeps_ci_install_small():
    runtime = (ROOT / "requirements-runtime.txt").read_text()
    assert "playwright" not in runtime and "pytest" not in runtime and "httpx" in runtime
    assert "-r requirements-runtime.txt" in (ROOT / "requirements.txt").read_text()


def test_refresh_falls_back_to_a_local_crawl_when_the_feed_is_unavailable(store, monkeypatch):
    monkeypatch.setattr(config, "FEED_REPO_URL", "git@github.com:me/data.git")

    def broken(s, **kw):
        raise FeedError("could not fetch the 'feed' branch")

    monkeypatch.setattr(feed, "run_sync", broken)
    monkeypatch.setattr(feed, "run_pipeline", lambda s, **kw: {"sources": {}, "errors": []})
    stats = refresh(store)
    assert any("crawled locally instead" in e and "'feed' branch" in e for e in stats["errors"])


def test_refresh_does_not_swallow_non_feed_errors(store, monkeypatch):
    monkeypatch.setattr(config, "FEED_REPO_URL", "git@github.com:me/data.git")
    monkeypatch.setattr(feed, "run_sync", lambda s, **kw: (_ for _ in ()).throw(PipelineBusy("busy")))
    with pytest.raises(PipelineBusy):
        refresh(store)


def test_git_error_message_names_the_next_step(tmp_path):
    with pytest.raises(FeedError, match="Has the GitHub Action run yet"):
        fetch_feed(str(tmp_path / "nope.git"), tmp_path / "cache", _validate=False)
