"""Pipeline tests with the network mocked (respx): isolation of failures, filtering, idempotency, closing."""
import asyncio

import httpx
import pytest
import respx

from app import pipeline
from app.pipeline import PipelineBusy, crawl, is_relevant, rescore, run_pipeline
from app.sources.http import make_client
from tests.conftest import make_job

GH_URL = "https://boards-api.greenhouse.io/v1/boards/{}/jobs"
LV_URL = "https://api.lever.co/v0/postings/{}"


def gh_payload(*titles_locs):
    return {"jobs": [
        {"id": i + 1, "title": t, "absolute_url": f"https://gh/{i}", "company_name": "Acme", "location": {"name": loc},
         "content": "React Node.js MongoDB", "first_published": "2026-10-01T00:00:00Z"}
        for i, (t, loc) in enumerate(titles_locs)]}


COMPANIES = {"greenhouse": ["acme", "ghost"], "lever": ["lvco"], "ashby": [], "names": {"lvco": "Lever Co"}, "aggregators": {}}


def run(coro):
    return asyncio.run(coro)


def test_is_relevant(profile):
    assert is_relevant(make_job(title="Backend Engineer", location="Pune"), profile)
    assert is_relevant(make_job(title="Backend Engineer", location="Remote"), profile)
    assert not is_relevant(make_job(title="Backend Engineer", location="Austin, TX"), profile)
    assert not is_relevant(make_job(title="Backend Engineer", location="Remote - US"), profile)
    assert not is_relevant(make_job(title="Office Manager", location="Pune"), profile)
    assert is_relevant(make_job(title="Backend Engineer", location="Austin, TX"), {"locations": []})
    assert is_relevant(make_job(title="Backend Engineer", location="Austin"), None)


def _mock_network():
    respx.get(GH_URL.format("acme")).mock(return_value=httpx.Response(200, json=gh_payload(
        ("Software Engineer I", "Bengaluru, India"), ("Account Executive", "Bengaluru, India"), ("Backend Engineer", "Austin, TX"))))
    respx.get(GH_URL.format("ghost")).mock(return_value=httpx.Response(404))
    respx.get(LV_URL.format("lvco")).mock(return_value=httpx.Response(200, json=[
        {"id": "l1", "text": "Full Stack Developer", "hostedUrl": "https://lv/l1", "categories": {"location": "Remote"}, "descriptionPlain": "React"}]))


@respx.mock
def test_crawl_isolates_failures_filters_and_names(store, profile):
    _mock_network()

    async def go():
        async with make_client() as c:
            return await crawl(store, COMPANIES, profile, client=c)

    stats = run(go())
    assert len(stats["errors"]) == 1 and "greenhouse:ghost" in stats["errors"][0]
    assert stats["sources"]["greenhouse"]["fetched"] == 3 and stats["sources"]["greenhouse"]["kept"] == 1
    titles = {j["title"]: j for j in store.query_jobs(min_score=0)["items"]}
    assert set(titles) == {"Software Engineer I", "Full Stack Developer"}
    assert titles["Full Stack Developer"]["company"] == "Lever Co"


@respx.mock
def test_crawl_is_idempotent_and_closes_disappeared_jobs(store, profile):
    _mock_network()

    async def go():
        async with make_client() as c:
            return await crawl(store, COMPANIES, profile, client=c)

    first = run(go())
    second = run(go())
    assert sum(s["inserted"] for s in first["sources"].values()) == 2
    assert sum(s["inserted"] for s in second["sources"].values()) == 0
    # The GH job disappears from the board -> closed, and status choices on others survive
    respx.get(GH_URL.format("acme")).mock(return_value=httpx.Response(200, json=gh_payload(("Software Engineer II", "Pune, India"))))
    third = run(go())
    assert third["sources"]["greenhouse"]["closed"] == 1
    open_titles = {j["title"] for j in store.query_jobs()["items"]}
    assert "Software Engineer I" not in open_titles and "Software Engineer II" in open_titles


@respx.mock
def test_all_sources_failing_marks_run_failed(store, monkeypatch, tmp_path, profile):
    respx.get(GH_URL.format("acme")).mock(return_value=httpx.Response(404))
    cfg = tmp_path / "c.json"
    cfg.write_text('{"greenhouse": ["acme"], "lever": [], "ashby": [], "aggregators": {}}')
    store.save_profile(profile)
    stats = run_pipeline(store, companies_path=cfg)
    assert stats["errors"] and store.recent_runs(1)[0]["status"] == "failed"


@respx.mock
def test_run_pipeline_end_to_end_scores_and_records_run(store, tmp_path):
    _mock_network()
    cfg = tmp_path / "c.json"
    import json
    cfg.write_text(json.dumps(COMPANIES))
    resume = tmp_path / "resume.txt"
    from tests.conftest import RESUME_TEXT
    resume.write_text(RESUME_TEXT)
    stats = run_pipeline(store, companies_path=cfg, resume=resume)
    assert stats["rescored"] == 2
    top = store.query_jobs()["items"][0]
    assert top["score"] > 50 and top["matched"]
    run_rec = store.recent_runs(1)[0]
    assert run_rec["status"] == "ok" and run_rec["finished_at"]
    assert store.load_profile()["name"] == "Jane Doe"


def test_run_pipeline_refuses_when_busy(store, tmp_path):
    store.start_run()
    with pytest.raises(PipelineBusy):
        run_pipeline(store, companies_path=tmp_path / "none.json")


def test_run_pipeline_missing_resume_fails_cleanly(store, tmp_path):
    from app.resume import ResumeError
    with pytest.raises(ResumeError):
        run_pipeline(store, companies_path=tmp_path / "none.json", resume=tmp_path / "nope.pdf")
    assert store.recent_runs(1)[0]["status"] == "failed" and store.running_run() is None


def test_rescore_updates_rows(store, profile):
    store.upsert_jobs([make_job(title="Full Stack Developer", description="React Node Express MongoDB")])
    assert store.query_jobs()["items"][0]["score"] == 0
    assert rescore(store, profile) == 1
    assert store.query_jobs()["items"][0]["score"] > 50


def test_real_companies_json_is_well_formed():
    cfg = pipeline.load_companies()
    for key in ("greenhouse", "lever", "ashby"):
        assert cfg[key] and len(cfg[key]) == len(set(cfg[key])), key
        assert all(t == t.strip().lower() and " " not in t for t in cfg[key])
    assert "himalayas" in cfg["aggregators"]
