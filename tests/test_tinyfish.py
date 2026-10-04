"""TinyFish discovery (Wellfound + YC). All HTTP is mocked; real page layouts are trimmed copies of live pages."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from app.pipeline import crawl, is_relevant, rescore
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
    assert remote.remote is True and remote.location == "Remote – Everywhere"       # the job's real scope, not the office town


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


@respx.mock
def test_rejected_pages_are_not_fetched_again(store, profile):
    """Stale / US-only / wrong-place pages are remembered so the daily run doesn't pay to re-fetch them."""
    stale, good = "https://wellfound.com/jobs/1-developer", "https://wellfound.com/jobs/2-developer"
    _mock_search((stale, "Developer at A • Pune"), (good, "Developer at B • Pune"))
    fetch = _mock_fetch({stale: WF_PAGE.replace("Posted: 2 weeks ago", "Posted: 3 years ago"), good: WF_PAGE})
    companies = {"greenhouse": [], "lever": [], "ashby": [], "aggregators": {"tinyfish": {**CFG, "enabled": True}}}

    async def go():
        async with make_client() as c:
            return await crawl(store, companies, profile, client=c)

    first = run(go())
    assert first["sources"]["tinyfish"]["fetched"] == 2 and first["sources"]["tinyfish"]["stale"] == 1
    assert store.rejected_urls() == {stale}
    calls_before = fetch.call_count
    second = run(go())
    assert fetch.call_count == calls_before and second["sources"]["tinyfish"]["fetched"] == 0      # nothing left to fetch


def test_rejected_memory_expires(store):
    store.add_rejected(["https://w/1"])
    assert store.rejected_urls() == {"https://w/1"}
    with store.conn() as c:
        c.execute("UPDATE rejected SET at='2020-01-01T00:00:00+00:00'")
    assert store.rejected_urls() == set()                      # old enough to be reconsidered (and purged)
    store.add_rejected(["https://w/1"])
    store.add_rejected(["https://w/1"])                        # idempotent
    assert store.rejected_urls() == {"https://w/1"}


# ---------------------------------------------------------------------------------------------- Nexorlio regression
# Real case: the search result labels the job with the COMPANY's city ("… at Nexorlio • Las Vegas") but the posting itself is
# "Remote only • Everywhere". It used to be thrown away before being opened.
NEXORLIO_URL = "https://wellfound.com/jobs/4803694-junior-full-stack-developer"
NEXORLIO_PAGE = """Nexorlio

Building scalable software and AI solutions for global businesses

# Junior Full-Stack Developer

* $24k – $28k • No equity
* |Remote (

  Everywhere

  )
* |1 year of exp
* |Full Time

Posted: yesterday• Recruiter recently active

Hires remotely in

Everywhere

Remote Work Policy

Remote only

Company Location

Las Vegas

Visa Sponsorship

Not Available

RelocationNot Allowed

Skills

Python

Javascript

React.js

## About the job

**Company:** Nexorlio

**Location:** Fully Remote — Worldwide

Build web apps with React and Node.js.
"""
ONSITE_PAGE = NEXORLIO_PAGE.replace("Remote (\n\n  Everywhere\n\n  )", "\n\n  Las Vegas").replace("Remote only", "In office").replace(
    "**Location:** Fully Remote — Worldwide", "**Location:** Las Vegas, NV (on-site)").replace("Hires remotely in\n\nEverywhere\n\n", "Job Location\n\nLas Vegas\n\n")


def test_parse_nexorlio_page_exactly():
    d = tf.parse_detail(NEXORLIO_PAGE)
    assert d["company"] == "Nexorlio" and d["location"] == "Remote – Everywhere" and d["remote_policy"] == "Remote only"
    assert d["experience"] == "1 year of exp" and d["employment_type"] == "Full Time" and d["salary"] == "$24k – $28k"
    assert d["skills"] == ["Python", "Javascript", "React.js"] and d["posted_text"] == "yesterday" and d["dead"] is False


@pytest.mark.parametrize("header,expected", [
    ("* |Remote (\n\n  India\n\n  )\n* |5 years of exp", "Remote – India"),
    ("* |Remote ( . Everywhere. )\n* |1 year of exp", "Remote – Everywhere"),
    ("* |Remote (\n\n  Indore\n\n  +1) • \n\n  Indore\n* |2 years of exp", "Remote – Indore +1 Indore"),
    ("* |Remote ()\n* |No experience required", "Remote"),
    ("* |\n\n  New Delhi\n* |No experience required", ""),               # onsite: comes from "Job Location" instead
])
def test_wellfound_header_locations(header, expected):
    page = f"Co\n\n# Dev\n\n* ₹10L – ₹12L\n{header}\n* |Full Time\n\nPosted: 1 day ago\n\nJob Location\n\nNew Delhi\n"
    loc = tf.parse_detail(page)["location"]
    assert loc == (expected or "New Delhi")
    assert "* |" not in loc and not loc.startswith(("|", "*"))                  # the original garbage can never come back


def test_empty_sections_never_leak_the_next_label_into_a_value():
    page = "C\n\n# Dev\n\n* |Remote ()\n* |Full Time\n\nPosted: 1 day ago\n\nHires remotely in\n\nRemote Work Policy\n\nRemote only\n"
    d = tf.parse_detail(page)
    assert d["location"] == "Remote" and d["remote_policy"] == "Remote only"


@respx.mock
def test_remote_job_labelled_with_the_companys_city_is_fetched_and_kept():
    s = respx.get(tf.SEARCH_URL).mock(return_value=httpx.Response(200, json=results((NEXORLIO_URL, "Junior Full-Stack Developer at Nexorlio • Las Vegas"))))
    f = _mock_fetch({NEXORLIO_URL: NEXORLIO_PAGE})
    info = {}
    jobs = run(_discover(info=info))
    assert [j.url for j in jobs] == [NEXORLIO_URL] and s.called and f.called
    (job,) = jobs
    assert (job.company, job.location, job.remote, job.salary) == ("Nexorlio", "Remote – Everywhere", True, "$24k – $28k")
    assert "Entry-level" not in job.tags and "Python" in job.tags
    assert info["fetched"] == 1 and info["city_label_ignored"] == 1               # the counter proves the old rule would have dropped it


@respx.mock
def test_onsite_lookalike_is_read_after_fetching_and_shows_its_real_location():
    """discover() only reports what the page says; the pipeline's relevance filter then rejects it (see the end-to-end test below)."""
    respx.get(tf.SEARCH_URL).mock(return_value=httpx.Response(200, json=results((NEXORLIO_URL, "Junior Full-Stack Developer at Nexorlio • Las Vegas"))))
    f = _mock_fetch({NEXORLIO_URL: ONSITE_PAGE})
    info = {}
    (job,) = run(_discover(info=info))
    assert f.called and info["fetched"] == 1
    assert job.location == "Las Vegas" and job.remote is False and not relevant_india(job)


@respx.mock
def test_remote_but_us_only_posting_is_still_rejected_after_fetching():
    page = NEXORLIO_PAGE.replace("Build web apps", "Must be located in the United States. Build web apps")
    respx.get(tf.SEARCH_URL).mock(return_value=httpx.Response(200, json=results((NEXORLIO_URL, "Junior Full-Stack Developer at Nexorlio • Las Vegas"))))
    _mock_fetch({NEXORLIO_URL: page})
    info = {}
    assert run(_discover(info=info)) == [] and info["us_only"] == 1


def test_fetch_priority_puts_india_remote_and_early_career_first():
    mk = lambda title, loc="": make_job(source="wellfound", title=title, location=loc)   # noqa: E731
    jobs = [mk("Staff Engineer", "Austin"), mk("Junior Developer", "Austin"), mk("Developer", "Pune, India"), mk("Developer", "Remote")]
    assert [j.title + "|" + j.location for j in sorted(jobs, key=tf.fetch_priority)] == [
        "Developer|Pune, India", "Developer|Remote", "Junior Developer|Austin", "Staff Engineer|Austin"]


@respx.mock
def test_when_the_fetch_cap_bites_the_promising_results_are_read_first():
    urls = [f"https://wellfound.com/jobs/{i}-developer" for i in range(1, 7)]
    titles = ["Developer at A • Austin", "Developer at B • Austin", "Developer at C • Pune", "Junior Developer at D • Austin",
              "Developer at E • Remote", "Developer at F • Austin"]
    respx.get(tf.SEARCH_URL).mock(return_value=httpx.Response(200, json=results(*zip(urls, titles))))
    fetch = _mock_fetch({u: WF_PAGE for u in urls})
    run(_discover(cfg={**CFG, "max_fetch": 3}))
    fetched = [u for c in fetch.calls for u in json.loads(c.request.content)["urls"]]
    assert set(fetched) == {urls[2], urls[4], urls[3]}                              # Pune, Remote, then the junior title


@respx.mock
def test_pipeline_stores_the_remote_nexorlio_job_but_not_an_onsite_look_alike(store, profile):
    """End to end through crawl(): the same search label ('Las Vegas'), two different pages, two different outcomes."""
    remote_url, onsite_url = NEXORLIO_URL, "https://wellfound.com/jobs/999-junior-full-stack-developer"
    respx.get(tf.SEARCH_URL).mock(return_value=httpx.Response(200, json=results(
        (remote_url, "Junior Full-Stack Developer at Nexorlio • Las Vegas"), (onsite_url, "Junior Full-Stack Developer at Vegasco • Las Vegas"))))
    _mock_fetch({remote_url: NEXORLIO_PAGE, onsite_url: ONSITE_PAGE})
    companies = {"greenhouse": [], "lever": [], "ashby": [], "aggregators": {"tinyfish": {**CFG, "enabled": True}}}

    async def go():
        async with make_client() as c:
            return await crawl(store, companies, profile, client=c)

    stats = run(go())
    rescore(store, profile)                                                                     # run_pipeline() scores after crawling
    (job,) = store.query_jobs(min_score=0)["items"]
    assert job["url"] == remote_url and job["company"] == "Nexorlio" and job["location"] == "Remote – Everywhere"
    assert job["remote"] is True and job["level"] == "entry" and job["salary_lpa"] == "≈ ₹21.1–24.6 LPA"
    assert job["posted_at"] is not None and store.rejected_urls() == {onsite_url}            # the onsite one is remembered, not re-fetched tomorrow
    assert stats["sources"]["tinyfish"]["fetched"] == 2 and stats["sources"]["tinyfish"]["inserted"] == 1


# ------------------------------------------------------------------------------------- listing pages + adaptive paging
ROLE_URL = "https://wellfound.com/role/r/full-stack-developer"
ROLE_RESULT = {"url": ROLE_URL, "text": "# Remote Full Stack Developer Jobs\n\n## Nexorlio\n\nJunior Full-Stack DeveloperFull-time\n",
               "links": [NEXORLIO_URL + "?utm=x", "https://wellfound.com/jobs/1742128-full-stack-developer", "https://wellfound.com/company/nexorlio",
                         "https://wellfound.com/role/r/react-developer", "https://www.workatastartup.com/jobs/100954", "https://example.com/jobs/5"]}


def test_job_links_are_harvested_from_a_wellfound_listing_page_only():
    jobs = tf.job_links_from_page(ROLE_RESULT)
    assert sorted(j.url for j in jobs) == sorted([NEXORLIO_URL, "https://wellfound.com/jobs/1742128-full-stack-developer"])   # no company/role/YC/other links
    nex = next(j for j in jobs if j.external_id == "4803694")
    assert nex.title == "Junior Full Stack Developer" and nex.source == "wellfound" and tf.PREVIEW_TAG in nex.tags
    assert tf.job_links_from_page({"url": ROLE_URL, "text": "[Dev](https://wellfound.com/jobs/77-dev) and (https://wellfound.com/jobs/77-dev)"})[0].external_id == "77"
    assert tf.job_links_from_page({}) == []


@respx.mock
def test_role_pages_feed_the_same_fetch_filter_pipeline():
    respx.get(tf.SEARCH_URL).mock(return_value=httpx.Response(200, json=results()))                       # search finds nothing at all
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if body.get("links"):                                                                              # listing page request
            return httpx.Response(200, json={"results": [ROLE_RESULT], "errors": []})
        res = [{"url": u, "text": NEXORLIO_PAGE if "4803694" in u else WF_PAGE} for u in body["urls"]]
        return httpx.Response(200, json={"results": res, "errors": []})

    respx.post(tf.FETCH_URL).mock(side_effect=handler)
    info = {}
    jobs = run(_discover(cfg={**CFG, "role_pages": [ROLE_URL, "https://evil.example/role/x", "https://wellfound.com/jobs/1-direct"]}, info=info))
    assert {j.url for j in jobs} == {NEXORLIO_URL, "https://wellfound.com/jobs/1742128-full-stack-developer"}
    nex = next(j for j in jobs if j.external_id == "4803694")
    assert nex.company == "Nexorlio" and nex.location == "Remote – Everywhere"                           # filled in from the posting, not the slug
    assert info["role_pages_ok"] == 1 and info["role_jobs"] == 2 and info["fetched"] == 2
    listing_requests = [c for c in calls if c.get("links")]
    assert len(listing_requests) == 1 and listing_requests[0]["urls"] == [ROLE_URL]                      # non-role URLs were never sent


@respx.mock
def test_role_page_failure_does_not_lose_search_results():
    u = "https://wellfound.com/jobs/1-developer"
    respx.get(tf.SEARCH_URL).mock(return_value=httpx.Response(200, json=results((u, "Developer at A • Pune"))))

    def handler(request):
        if json.loads(request.content).get("links"):
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"results": [{"url": u, "text": WF_PAGE}], "errors": []})

    respx.post(tf.FETCH_URL).mock(side_effect=handler)
    info = {}
    jobs = run(_discover(cfg={**CFG, "role_pages": [ROLE_URL]}, info=info))
    assert [j.url for j in jobs] == [u] and any("role pages" in e for e in info["errors"])


@respx.mock
def test_adaptive_paging_stops_when_a_page_is_mostly_known():
    def page_of(prefix):
        return results(*[(f"https://wellfound.com/jobs/{prefix}{i}-developer", "Developer at A • Pune") for i in range(10)])

    s = respx.get(tf.SEARCH_URL).mock(side_effect=lambda req: httpx.Response(200, json=page_of(int(req.url.params["page"]) * 100)))
    known = {f"https://wellfound.com/jobs/{p * 100}{i}-developer": True for p in (0, 1) for i in range(10)}   # pages 0 and 1 are old news
    _mock_fetch({})
    info = {}
    run(_discover(cfg={**CFG, "pages": 5}, known=known, info=info))
    assert s.call_count == 1 and info["search_calls"] == 1 and info["paging_stopped"] == 1                 # page 0 had nothing new -> stop


@respx.mock
def test_adaptive_paging_keeps_going_while_pages_bring_new_jobs():
    s = respx.get(tf.SEARCH_URL).mock(side_effect=lambda req: httpx.Response(200, json=results(
        *[(f"https://wellfound.com/jobs/{int(req.url.params['page'])}{i}0-developer", "Developer at A • Pune") for i in range(10)])))
    _mock_fetch({})
    info = {}
    run(_discover(cfg={**CFG, "pages": 4}, info=info))
    assert s.call_count == 4 and info["paging_stopped"] == 0 and info["found"] == 40


# ----------------------------------------------------------------------------------- YC "Work at a Startup" header layouts
YC_LIVE_SPRUCE = ("# Mobile Engineer, Digital Identity (Fully Remote) at SpruceID(W21)\n\n$120K - $200K\n\nSpruceID lets users control their data across the web.\n\n"
                  "United States / Remote (US)Full-timeUS citizen/visa only3+ years\n\nApply now\n\nAbout SpruceID\n\n5+ years of experience shipping **production mobile applications**\n")


@pytest.mark.parametrize("page,loc,exp,etype", [
    (YC_LIVE_SPRUCE, "United States / Remote (US)", "3+ years", "Full-time"),       # description sentence must not override the header
    ("# Junior Full Stack Engineer at Acme(S24)\n\n$40K - $70K\n\nWe build things.\n\nRemote (Worldwide)Full-timeUS citizenship/visa not required1+ years\n\nApply now\n",
     "Remote (Worldwide)", "1+ years", "Full-time"),
    ("# Software Engineer, EHR (India) at Athelas(S16)\n\nExtensible tech\n\nGurugram, IndiaFull-timeUS citizen/visa onlyAny (new grads ok)\n\nApply now\n",
     "Gurugram, India", "Any (new grads ok)", "Full-time"),
    ("SuperKalam · W23\n\n# Mobile Engineer\n\nAI-Powered\n\nLocationBengaluru, KA, IN\n\nTypeFull-time\n\nExperience1+ years\n\nApply to this role\n",
     "Bengaluru, KA, IN", "1+ years", "Full-time"),
])
def test_yc_header_layouts(page, loc, exp, etype):
    d = tf.parse_detail(page)
    assert (d["location"], d["experience"], d["employment_type"]) == (loc, exp, etype)


def test_a_yc_page_is_never_mistaken_for_a_wellfound_header():
    """YC pages also start with '# Title'; sentences below must not be read as the Wellfound bullet header (this hid every YC location)."""
    assert tf._wf_header(YC_LIVE_SPRUCE) == {}
    assert tf._wf_header(NEXORLIO_PAGE)["work_location"] == "Remote – Everywhere"


def test_yc_remote_worldwide_junior_role_is_usable_from_india():
    page = "# Junior Full Stack Engineer at Acme(S24)\n\n$40K - $70K\n\nWe build things.\n\nRemote (Worldwide)Full-timeUS citizenship/visa not required1+ years\n\nApply now\n"
    job = tf.apply_detail(make_job(source="yc", title="Junior Full Stack Engineer", location="", tags=[tf.PREVIEW_TAG]), tf.parse_detail(page))
    assert job.remote is True and relevant_india(job) and not tf.looks_us_only(job)
    us = tf.apply_detail(make_job(source="yc", title="Mobile Engineer", location="", tags=[tf.PREVIEW_TAG]), tf.parse_detail(YC_LIVE_SPRUCE))
    assert us.location == "United States / Remote (US)" and not relevant_india(us)                  # US-restricted remote stays out


def test_company_name_comes_from_the_right_place_on_each_site():
    assert tf.parse_detail("# Mobile Engineer (Fully Remote) at SpruceID(W21)\n\n$1\n\nRemote (US)Full-time3+ years\n")["company"] == "SpruceID"
    assert tf.parse_detail("SuperKalam · W23\n\n# Mobile Engineer\n\nLocationBengaluru, KA, IN\n")["company"] == "SuperKalam"
    assert tf.parse_detail(NEXORLIO_PAGE)["company"] == "Nexorlio"
    assert "company" not in tf.parse_detail("# Software Engineer\n\nLocationPune, IN\n")             # never a markdown heading


def test_interleave_shares_the_budget_between_sources_and_keeps_each_sources_order():
    mk = lambda src, n: make_job(source=src, external_id=str(n), title=f"{src}{n}", url=f"https://{src}/{n}")   # noqa: E731
    jobs = [mk("wellfound", i) for i in range(5)] + [mk("yc", i) for i in range(2)]
    assert [j.title for j in tf.interleave_by_source(jobs)] == ["wellfound0", "yc0", "wellfound1", "yc1", "wellfound2", "wellfound3", "wellfound4"]
    assert tf.interleave_by_source([]) == []


@respx.mock
def test_a_huge_wellfound_backlog_cannot_starve_yc_of_the_fetch_budget():
    wf = [f"https://wellfound.com/jobs/{i}-developer" for i in range(1, 21)]
    yc = [f"https://www.workatastartup.com/jobs/{i}" for i in range(500, 505)]
    respx.get(tf.SEARCH_URL).mock(return_value=httpx.Response(200, json=results(*[(u, "Developer at A • Pune") for u in wf], *[(u, "Software Engineer at B(W21)") for u in yc])))
    fetch = _mock_fetch({u: WF_PAGE for u in wf + yc})
    run(_discover(cfg={**CFG, "max_fetch": 10}))
    fetched = [u for c in fetch.calls for u in json.loads(c.request.content)["urls"]]
    assert len(fetched) == 10 and sum("workatastartup" in u for u in fetched) == 5          # all 5 YC ones made it despite 20 Wellfound candidates


@respx.mock
def test_a_removed_posting_is_remembered_but_a_temporary_failure_is_retried(store, profile):
    gone, blocked, good = "https://www.workatastartup.com/jobs/1", "https://wellfound.com/jobs/2-developer", "https://wellfound.com/jobs/3-developer"
    respx.get(tf.SEARCH_URL).mock(return_value=httpx.Response(200, json=results((gone, "Software Engineer at A(W21)"), (blocked, "Developer at B • Pune"), (good, "Developer at C • Pune"))))
    respx.post(tf.FETCH_URL).mock(return_value=httpx.Response(200, json={
        "results": [{"url": good, "text": WF_PAGE}], "errors": [{"url": gone, "error": "page_not_found"}, {"url": blocked, "error": "bot_blocked"}]}))
    companies = {"greenhouse": [], "lever": [], "ashby": [], "aggregators": {"tinyfish": {**CFG, "enabled": True}}}

    async def go():
        async with make_client() as c:
            return await crawl(store, companies, profile, client=c)

    stats = run(go())
    assert stats["sources"]["tinyfish"]["gone"] == 1
    assert gone in store.rejected_urls() and blocked not in store.rejected_urls()           # dead link: never again; blocked: try again next run
    assert {j["url"] for j in store.query_jobs(min_score=0)["items"]} == {good, blocked}     # blocked one kept as a Pune-labelled preview


@pytest.mark.parametrize("err,gone", [("page_not_found", True), ("HTTP 404", True), ("410 Gone", True), ("bot_blocked", False), ("timeout", False), ("", False)])
def test_which_fetch_errors_count_as_a_removed_posting(err, gone):
    assert bool(tf.GONE.search(err)) is gone
