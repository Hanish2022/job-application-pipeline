"""TinyFish discovery (Wellfound + YC). All HTTP is mocked; real page layouts are trimmed copies of live pages."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from app.pipeline import crawl, is_relevant
from app.sources import tinyfish as tf
from app.sources.http import make_client
from tests.conftest import make_job

WF_PAGE = """Vite

Actively Hiring

digital payments

# Software Engineer Intern

* ₹1.2L – ₹6L • No equity
* |

  New Delhi
* |No experience required
* |Internship

Posted: 2 weeks ago• Recruiter recently active

Job Location

New Delhi

Remote Work Policy

In office

Skills

SQL/MySQL/PostgreSQL

React/Tanstack Query

## About the job

\\**Company Description \\**

Vite Knowledge builds React and Node.js products.
"""

WF_REMOTE = """Better

# Associate Software Engineer

* ₹1.2L – ₹1.8L • No equity
* |Remote ( . Everywhere. )
* |No experience required

Posted: 3 days ago

Job Location

India Gate

Remote Work Policy

Remote

## About the job

Build things with React.
"""

YC_COMPACT = """# Software Engineer (India) at Strac(W22)

₹2.5M - ₹5M INR  •  0.01% - 0.10%

Data Discovery, DLP, DSPM for SaaS

Bengaluru, KA, IN / Pune, MH, IN / Mumbai, MH, INFull-timeUS citizenship/visa not required3+ years

Apply now

About Strac

## What is Strac?

We build security tools in TypeScript and React.
"""

YC_FREE = """AnswerThis · F25

Product Engineer (Full-Time, Remote — India)

Location: Remote within India (must be available during U.S. working hours)

Compensation: ₹20–35 lakh/year (based on experience)

About AnswerThis

AI research platform built with Python and React.
"""

YC_LABELLED = """Swadesh · S19

# Senior Software Engineer - Backend

LocationNew Delhi, DL, IN / Gurugram, HR, IN

TypeFull-time

Salary$25 - $40

Experience6+ years

VisaUS citizenship/visa not required

## About the role

Backend work.
"""


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ URL + title parsing
def test_identify_only_individual_postings():
    assert tf.identify("https://wellfound.com/jobs/4718621-software-engineer-intern?utm=x#y") == (
        "wellfound", "4718621", "https://wellfound.com/jobs/4718621-software-engineer-intern")
    assert tf.identify("https://www.workatastartup.com/jobs/100954")[:2] == ("yc", "100954")
    for bad in ["https://wellfound.com/role/l/software-engineer/india", "https://wellfound.com/company/vite",
                "https://www.workatastartup.com/companies/strac", "https://example.com/jobs/123", "not a url"]:
        assert tf.identify(bad) is None, bad


@pytest.mark.parametrize("raw,expected", [
    ("Software Engineer Intern at Vite • New Delhi", ("Software Engineer Intern", "Vite", "New Delhi")),
    ("Full Stack Developer at WeKnow Group • Bangalore Urban", ("Full Stack Developer", "WeKnow Group", "Bangalore Urban")),
    ("Software Engineer (India) at Strac | Y Combinator's Work at ...", ("Software Engineer (India)", "Strac", "")),
    ("Software Engineer - Backend/Fullstack [India] at FurtherAI(W24)", ("Software Engineer - Backend/Fullstack [India]", "FurtherAI", "")),
    ("Associate Software Engineer at Better • India Gate • Remote (Work from …", ("Associate Software Engineer", "Better", "India Gate, Remote (Work from")),
    ("Something without company", ("Something without company", "", "")),
])
def test_parse_search_title(raw, expected):
    assert tf.parse_search_title(raw) == expected


def test_parse_search_skips_non_postings_and_blank_titles():
    data = {"results": [
        {"url": "https://wellfound.com/jobs/1-dev", "title": "Dev at Acme • Pune", "snippet": "<b>Great</b> job"},
        {"url": "https://wellfound.com/company/acme", "title": "Acme"},
        {"url": "https://wellfound.com/jobs/2-x", "title": ""},
        {"url": "https://other.com/jobs/3", "title": "Dev at X"},
    ]}
    (job,) = tf.parse_search(data)
    assert (job.source, job.external_id, job.company, job.location, job.description) == ("wellfound", "1", "Acme", "Pune", "Great job")
    assert tf.PREVIEW_TAG in job.tags


# ------------------------------------------------------------------ page parsing
def test_parse_wellfound_page():
    d = tf.parse_detail(WF_PAGE)
    assert d["company"] == "Vite" and d["location"] == "New Delhi" and d["remote_policy"] == "In office"
    assert d["salary"] == "₹1.2L – ₹6L" and d["employment_type"] == "Internship" and d["experience"] == "No experience required"
    assert d["skills"] == ["SQL/MySQL/PostgreSQL", "React/Tanstack Query"]
    assert "React and Node.js" in d["description"] and "Company Description" in d["description"] and "\\*" not in d["description"]
    assert d["dead"] is False
    posted = datetime.fromisoformat(d["posted_at"])
    assert 12 < (datetime.now(timezone.utc) - posted).days < 16


def test_parse_yc_layouts():
    a = tf.parse_detail(YC_COMPACT)
    assert a["location"].startswith("Bengaluru, KA, IN") and a["experience"] == "3+ years"
    assert a["salary"] == "₹2.5M - ₹5M INR" and a["employment_type"].lower() == "full-time"
    b = tf.parse_detail(YC_FREE)
    assert b["location"].startswith("Remote within India") and b["salary"].startswith("₹20–35 lakh")
    c = tf.parse_detail(YC_LABELLED)
    assert c["location"] == "New Delhi, DL, IN / Gurugram, HR, IN" and c["experience"] == "6+ years"


def test_dead_markers_detected_only_near_top():
    assert tf.parse_detail("# Dev\n\nThis job is no longer accepting applications.\n")["dead"] is True
    assert tf.parse_detail("Job not found")["dead"] is True
    long_text = "# Dev\n\n" + "x " * 2000 + "\nThe previous position has been filled by someone great"
    assert tf.parse_detail(long_text)["dead"] is False
    assert tf.parse_detail("")["description"] if False else tf.parse_detail("") == {}


def test_apply_detail_merges_and_tags():
    job = make_job(source="wellfound", title="Software Engineer Intern", company="Unknown", location="", description="snippet", tags=[tf.PREVIEW_TAG])
    tf.apply_detail(job, tf.parse_detail(WF_PAGE))
    assert job.company == "Vite" and job.location == "New Delhi" and job.salary == "₹1.2L – ₹6L"
    assert "Internship" not in job.description.split("\n")[0] and job.description.startswith("No experience required")
    assert tf.PREVIEW_TAG not in job.tags and {"Entry-level", "Intern"} <= set(job.tags) and "React/Tanstack Query" in job.tags
    remote = make_job(source="wellfound", location="India Gate", tags=[tf.PREVIEW_TAG])
    tf.apply_detail(remote, tf.parse_detail(WF_REMOTE))
    assert remote.remote is True and remote.location == "Remote – India Gate"


def test_relative_time():
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    assert tf.relative_to_iso("2 weeks ago", now).startswith("2026-09-20")
    assert tf.relative_to_iso("yesterday", now).startswith("2026-10-03")
    assert tf.relative_to_iso("today", now).startswith("2026-10-04")
    assert tf.relative_to_iso("3 hours ago", now).startswith("2026-10-03") or tf.relative_to_iso("3 hours ago", now).startswith("2026-10-04")
    assert tf.relative_to_iso("sometime", now) is None


@pytest.mark.parametrize("text,expected", [
    ("US citizens only", True), ("US citizen/visa only", True), ("Open to US-only candidates", True),
    ("Must be located in the United States", True), ("authorized to work in the U.S.", True), ("Green card holder required", True),
    ("US citizenship/visa not required", False),                      # the opposite
    ("must be available during U.S. working hours", False),           # timezone overlap, not work authorisation
    ("We sponsor visas. Based in Bengaluru", False), ("Build USB drivers", False),
])
def test_us_only_detection(text, expected):
    assert tf.looks_us_only(make_job(description=text)) is expected


# ------------------------------------------------------------------ end-to-end discovery (mocked HTTP)
def results(*items):
    return {"results": [{"url": u, "title": t, "snippet": ""} for u, t in items], "total_results": len(items), "page": 0}


CFG = {"queries": ["q1"], "pages": 1, "max_fetch": 60, "max_age_days": 90}
relevant_india = lambda j: is_relevant(j, {"locations": ["india", "remote"]})  # noqa: E731


def _mock_search(*items):
    return respx.get(tf.SEARCH_URL).mock(return_value=httpx.Response(200, json=results(*items)))


def _mock_fetch(pages=None, errors=None):
    def handler(request):
        urls = json.loads(request.content)["urls"]
        res = [{"url": u, "text": pages[u]} for u in urls if u in (pages or {})]
        errs = [{"url": u, "error": (errors or {}).get(u, "bot_blocked")} for u in urls if u not in (pages or {})]
        return httpx.Response(200, json={"results": res, "errors": errs})
    return respx.post(tf.FETCH_URL).mock(side_effect=handler)


async def _discover(cfg=CFG, known=None, info=None):
    async with make_client() as c:
        async def nosleep(_): return None
        return await tf.discover(c, cfg, known or {}, relevant_india, info, sleep=nosleep)


@respx.mock
def test_discover_happy_path_sends_key_and_builds_jobs():
    u1, u2 = "https://wellfound.com/jobs/1-software-engineer-intern", "https://wellfound.com/jobs/2-associate-software-engineer"
    s = _mock_search((u1, "Software Engineer Intern at Vite • New Delhi"), (u2, "Associate Software Engineer at Better • India Gate"))
    f = _mock_fetch({u1: WF_PAGE, u2: WF_REMOTE})
    info = {}
    jobs = run(_discover(info=info))
    assert {j.external_id for j in jobs} == {"1", "2"}
    by = {j.external_id: j for j in jobs}
    assert by["1"].location == "New Delhi" and by["2"].remote and by["1"].salary == "₹1.2L – ₹6L"
    assert s.calls.last.request.headers["x-api-key"] == "test-key" and f.calls.last.request.headers["x-api-key"] == "test-key"
    assert info["found"] == 2 and info["fetched"] == 2 and info["dead"] == 0 and set(info["seen_urls"]) == {u1, u2}


@respx.mock
def test_discover_drops_dead_stale_us_only_and_irrelevant():
    base = "https://wellfound.com/jobs/"
    dead, stale, usonly, ok, sales = (base + f"{i}-dev" for i in range(1, 6))
    _mock_search((dead, "Developer at A • Pune"), (stale, "Developer at B • Pune"), (usonly, "Developer at C • Pune"), (ok, "Developer at D • Pune"),
                 (sales, "Sales Manager at E • Pune"))
    old_page = WF_PAGE.replace("Posted: 2 weeks ago", "Posted: 2 years ago")
    _mock_fetch({dead: "# Dev\n\nThis job is no longer accepting applications.", stale: old_page,
                 usonly: WF_PAGE + "\nMust be located in the United States.", ok: WF_PAGE, sales: WF_PAGE})
    info = {}
    jobs = run(_discover(info=info))
    assert [j.url for j in jobs] == [ok]
    assert (info["dead"], info["stale"], info["us_only"], info["fetched"]) == (1, 1, 1, 4)     # the sales title never cost a fetch


@respx.mock
def test_blocked_fetch_is_a_preview_only_when_search_gave_a_usable_location():
    a, b = "https://wellfound.com/jobs/1-dev", "https://wellfound.com/jobs/2-dev"
    _mock_search((a, "Full Stack Developer at A • Bangalore Urban"), (b, "Full Stack Developer at B"))
    _mock_fetch({})                                   # everything 'bot_blocked'
    info = {}
    jobs = run(_discover(info=info))
    assert [j.url for j in jobs] == [a] and tf.PREVIEW_TAG in jobs[0].tags and info["preview_only"] == 1


@respx.mock
def test_known_jobs_are_not_refetched_but_previews_are_retried():
    a, b = "https://wellfound.com/jobs/1-dev", "https://wellfound.com/jobs/2-dev"
    _mock_search((a, "Developer at A • Pune"), (b, "Developer at B • Pune"))
    fetch = _mock_fetch({b: WF_PAGE})
    jobs = run(_discover(known={a: True, b: False}))        # a fully known, b was only a preview last time
    assert [j.url for j in jobs] == [b]
    assert json.loads(fetch.calls.last.request.content)["urls"] == [b]


@respx.mock
def test_max_fetch_caps_calls_and_batches_of_ten():
    urls = [f"https://wellfound.com/jobs/{i}-dev" for i in range(1, 31)]
    _mock_search(*[(u, "Developer at A • Pune") for u in urls])
    fetch = _mock_fetch({u: WF_PAGE for u in urls})
    jobs = run(_discover(cfg={**CFG, "max_fetch": 25}))
    assert len(jobs) == 25 + 5 and fetch.call_count == 3                     # 10+10+5 fetched; 5 previews with a location
    assert sum(tf.PREVIEW_TAG in j.tags for j in jobs) == 5


@respx.mock
def test_search_failures_are_isolated_but_bad_key_is_fatal():
    good = "https://wellfound.com/jobs/1-dev"

    def handler(request):
        q = request.url.params["query"]
        if q == "bad":
            return httpx.Response(400)
        return httpx.Response(200, json=results((good, "Developer at A • Pune")))

    respx.get(tf.SEARCH_URL).mock(side_effect=handler)
    _mock_fetch({good: WF_PAGE})
    info = {}
    jobs = run(_discover(cfg={**CFG, "queries": ["bad", "good"]}, info=info))
    assert len(jobs) == 1 and info["search_errors"] == 1 and info["errors"]

    respx.get(tf.SEARCH_URL).mock(return_value=httpx.Response(401, text="invalid key"))
    with pytest.raises(RuntimeError, match="API key"):
        run(_discover())


# ------------------------------------------------------------------ pipeline integration
@respx.mock
def test_pipeline_runs_tinyfish_and_closes_vanished_jobs(store, profile, monkeypatch):
    u1 = "https://wellfound.com/jobs/1-software-engineer-intern"
    _mock_search((u1, "Software Engineer Intern at Vite • New Delhi"))
    _mock_fetch({u1: WF_PAGE})
    companies = {"greenhouse": [], "lever": [], "ashby": [], "aggregators": {"tinyfish": {**CFG, "enabled": True, "close_after_days": 21}}}

    async def go():
        async with make_client() as c:
            return await crawl(store, companies, profile, client=c)

    stats = run(go())
    assert stats["sources"]["tinyfish"]["inserted"] == 1 and not stats["errors"]
    job = store.query_jobs(min_score=0)["items"][0]
    assert job["source"] == "wellfound" and job["salary_lpa"] == "₹1.2–6 LPA" and job["location"] == "New Delhi"
    # second run: known => no detail fetch, still open
    fetch = _mock_fetch({})
    before = fetch.call_count                      # respx reuses the route object, so count the delta
    run(go())
    assert fetch.call_count == before and store.query_jobs()["total"] == 1
    # a job that stops appearing in search eventually closes
    with store.conn() as c:
        c.execute("UPDATE jobs SET last_seen='2020-01-01T00:00:00+00:00'")
    respx.get(tf.SEARCH_URL).mock(return_value=httpx.Response(200, json=results()))
    stats = run(go())
    assert stats["sources"]["tinyfish"]["closed"] == 1 and store.query_jobs()["total"] == 0


def test_pipeline_skips_tinyfish_without_key(store, profile, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "TINYFISH_API_KEY", "")
    companies = {"greenhouse": [], "lever": [], "ashby": [], "aggregators": {"tinyfish": {**CFG, "enabled": True}}}

    async def go():
        async with make_client() as c:
            return await crawl(store, companies, profile, client=c)

    stats = run(go())
    assert any("TINYFISH_API_KEY not set" in e for e in stats["errors"]) and "tinyfish" not in stats["sources"]
