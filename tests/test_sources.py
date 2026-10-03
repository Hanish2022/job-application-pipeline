"""Source parsers (pure) and fetchers (HTTP mocked with respx)."""
import asyncio

import httpx
import pytest
import respx

from app.sources import adzuna, arbeitnow, ashby, greenhouse, himalayas, lever, remotive
from app.sources.http import get_json, make_client


def run(coro):
    return asyncio.run(coro)


GH = {"jobs": [
    {"id": 11, "title": "SDE I", "absolute_url": "https://boards.greenhouse.io/acme/jobs/11", "company_name": "Acme",
     "location": {"name": "Bengaluru, India"}, "first_published": "2026-09-30T10:00:00-04:00",
     "content": "&lt;p&gt;Build with &lt;b&gt;React&lt;/b&gt;&lt;/p&gt;", "departments": [{"name": "Engineering"}]},
    {"id": 12, "title": "", "absolute_url": "https://x", "location": {"name": "x"}},          # invalid: no title
    {"id": 13, "title": "No location", "absolute_url": "https://x/13", "location": None, "content": None, "departments": None},
]}


def test_greenhouse_parse():
    jobs = greenhouse.parse(GH, "acme")
    assert [j.external_id for j in jobs] == ["11", "13"]
    j = jobs[0]
    assert (j.source, j.company, j.location, j.department, j.board) == ("greenhouse", "Acme", "Bengaluru, India", "Engineering", "acme")
    assert "Build with React" in j.description and "<" not in j.description
    assert j.posted_at == "2026-09-30T14:00:00+00:00"
    assert jobs[1].location == "" and jobs[1].description == ""


LV = [
    {"id": "a1", "text": "Backend Engineer", "hostedUrl": "https://jobs.lever.co/acme-co/a1", "createdAt": 1791009036000,
     "categories": {"location": "Pune", "allLocations": ["Pune", "Remote"], "team": "Platform", "commitment": "Full Time"},
     "descriptionPlain": "Node and SQL", "additionalPlain": "Benefits", "workplaceType": "remote",
     "lists": [{"text": "Requirements", "content": "<li>Git</li>"}], "salaryRange": {"min": 10, "max": 20, "currency": "USD", "interval": "year"}},
    {"id": "a2", "text": "Broken", "hostedUrl": None, "applyUrl": None},
]


def test_lever_parse():
    jobs = lever.parse(LV, "acme-co")
    assert len(jobs) == 1
    j = jobs[0]
    assert j.company == "Acme Co" and j.location == "Pune / Remote" and j.remote is True
    assert j.department == "Platform" and j.employment_type == "Full Time"
    assert "Node and SQL" in j.description and "Requirements" in j.description and "Git" in j.description
    assert j.salary.startswith("USD 10")
    assert lever.parse({"not": "a list"}, "x") == []


ASH = {"jobs": [
    {"id": "z1", "title": "Full Stack Engineer", "jobUrl": "https://jobs.ashbyhq.com/acme/z1", "location": "Remote",
     "secondaryLocations": [{"location": "Bengaluru"}], "isRemote": True, "publishedAt": "2026-09-01T00:00:00.000+00:00",
     "descriptionPlain": "React", "compensation": {"compensationTierSummary": None}, "team": "Eng", "employmentType": "FullTime"},
    {"id": "z2", "title": "Hidden", "jobUrl": "https://x/z2", "isListed": False},
]}


def test_ashby_parse_skips_unlisted_and_handles_null_salary():
    jobs = ashby.parse(ASH, "acme")
    assert len(jobs) == 1
    j = jobs[0]
    assert j.location == "Remote / Bengaluru" and j.remote and j.salary is not None and j.salary == ""
    assert j.department == "Eng"


def test_remotive_parse():
    data = {"jobs": [{"id": 5, "url": "https://remotive.com/5", "title": "Dev", "company_name": "R", "candidate_required_location": "Worldwide",
                      "description": "<p>Hi</p>", "publication_date": "2026-09-30T13:15:26", "tags": ["react"], "job_type": "full_time", "category": "Software"}]}
    (j,) = remotive.parse(data)
    assert j.location == "Remote – Worldwide" and j.remote and j.tags == ["react"] and j.description == "Hi"


def test_arbeitnow_parse():
    data = {"data": [{"slug": "s", "company_name": "C", "title": "Dev", "url": "https://a/s", "location": "Berlin", "remote": False,
                      "created_at": 1791009036, "description": "<p>x</p>", "job_types": ["full_time"], "tags": []}]}
    (j,) = arbeitnow.parse(data)
    assert j.external_id == "s" and j.employment_type == "full_time" and not j.remote


def test_adzuna_parse_and_enabled(monkeypatch):
    data = {"results": [{"id": "9", "title": "<b>Dev</b>", "company": {"display_name": "Z"}, "redirect_url": "https://adzuna/9",
                         "location": {"display_name": "Noida"}, "salary_min": 100000, "salary_max": 200000, "description": "x", "created": "2026-09-30T00:00:00Z"}]}
    (j,) = adzuna.parse(data, "in")
    assert j.title == "Dev" and j.salary == "100000–200000" and j.board == "adzuna-in"
    monkeypatch.setattr(adzuna.config, "ADZUNA_APP_ID", "")
    assert adzuna.enabled() is False
    monkeypatch.setattr(adzuna.config, "ADZUNA_APP_ID", "x")
    monkeypatch.setattr(adzuna.config, "ADZUNA_APP_KEY", "y")
    assert adzuna.enabled() is True


def test_himalayas_parse():
    data = {"jobs": [
        {"title": "Junior Dev", "companyName": "H", "guid": "https://himalayas.app/j/1", "applicationLink": "https://himalayas.app/j/1",
         "locationRestrictions": ["India"], "seniority": ["Entry-level"], "minSalary": "1000", "maxSalary": "2000", "currency": "USD",
         "salaryPeriod": "annual", "pubDate": "1791009036", "description": "<p>d</p>"},
        {"title": "World Dev", "companyName": "H", "guid": "g2", "applicationLink": "https://himalayas.app/j/2", "locationRestrictions": []},
    ]}
    a, b = himalayas.parse(data)
    assert a.location == "Remote – India" and a.tags == ["Entry-level"] and a.salary == "USD 1,000–2,000 annual"
    assert b.location == "Remote – Worldwide"


# ------------------------------------------------------------------ HTTP layer
@respx.mock
def test_fetch_greenhouse_hits_expected_url():
    route = respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(return_value=httpx.Response(200, json=GH))

    async def go():
        async with make_client() as c:
            return await greenhouse.fetch(c, "acme")

    jobs = run(go())
    assert len(jobs) == 2 and route.calls.last.request.url.params["content"] == "true"


@respx.mock
def test_get_json_retries_on_503_then_succeeds(monkeypatch):
    async def no_sleep(_): return None
    monkeypatch.setattr("app.sources.http.asyncio.sleep", no_sleep)
    route = respx.get("https://api.test/x").mock(side_effect=[httpx.Response(503), httpx.Response(429), httpx.Response(200, json={"ok": 1})])

    async def go():
        async with make_client() as c:
            return await get_json(c, "https://api.test/x")

    assert run(go()) == {"ok": 1} and route.call_count == 3


@respx.mock
def test_get_json_404_fails_fast_without_retry():
    route = respx.get("https://api.test/missing").mock(return_value=httpx.Response(404))

    async def go():
        async with make_client() as c:
            await get_json(c, "https://api.test/missing")

    with pytest.raises(httpx.HTTPStatusError):
        run(go())
    assert route.call_count == 1


@respx.mock
def test_get_json_gives_up_after_retries(monkeypatch):
    async def no_sleep(_): return None
    monkeypatch.setattr("app.sources.http.asyncio.sleep", no_sleep)
    route = respx.get("https://api.test/down").mock(return_value=httpx.Response(500))

    async def go():
        async with make_client() as c:
            await get_json(c, "https://api.test/down", retries=3)

    with pytest.raises(httpx.HTTPStatusError):
        run(go())
    assert route.call_count == 3


@respx.mock
def test_himalayas_skips_rejected_search_but_fails_if_all_rejected(monkeypatch):
    async def no_sleep(_): return None
    monkeypatch.setattr("app.sources.himalayas.asyncio.sleep", no_sleep)
    ok = {"jobs": [{"title": "Dev", "companyName": "H", "guid": "g", "applicationLink": "https://himalayas.app/g"}]}
    respx.get("https://himalayas.app/jobs/api/search", params={"q": "bad"}).mock(return_value=httpx.Response(400))
    respx.get("https://himalayas.app/jobs/api/search", params={"q": "good"}).mock(return_value=httpx.Response(200, json=ok))

    async def go(searches):
        async with make_client() as c:
            return await himalayas.fetch(c, searches, pages=2, delay=0)

    assert len(run(go([{"q": "bad"}, {"q": "good"}]))) == 1
    with pytest.raises(httpx.HTTPStatusError):
        run(go([{"q": "bad"}]))


def test_lever_structured_salary_and_intervals():
    item = dict(LV[0], salaryRange={"min": 100000, "max": 150000, "currency": "USD", "interval": "per-year-salary"})
    (j,) = lever.parse([item], "acme")
    assert (j.salary_min, j.salary_max, j.salary_currency, j.salary_period) == (100000, 150000, "USD", "year")
    item["salaryRange"] = {"min": 40, "max": 60, "currency": "USD", "interval": "per-hour-wage"}
    assert lever.parse([item], "acme")[0].salary_period == "hour"


def test_himalayas_structured_salary_fields():
    data = {"jobs": [{"title": "Dev", "companyName": "H", "guid": "g", "applicationLink": "https://himalayas.app/g",
                      "minSalary": "1800000", "maxSalary": "2200000", "currency": "INR", "salaryPeriod": "annual"},
                     {"title": "Dev2", "companyName": "H", "guid": "g2", "applicationLink": "https://himalayas.app/g2",
                      "minSalary": "30", "maxSalary": "40", "currency": "USD", "salaryPeriod": "hourly"},
                     {"title": "Dev3", "companyName": "H", "guid": "g3", "applicationLink": "https://himalayas.app/g3", "minSalary": None}]}
    a, b, c = himalayas.parse(data)
    assert (a.salary_min, a.salary_max, a.salary_currency, a.salary_period) == (1800000, 2200000, "INR", "year")
    assert b.salary_period == "hour" and c.salary_min is None


def test_adzuna_india_salary_is_inr():
    data = {"results": [{"id": "9", "title": "Dev", "company": {"display_name": "Z"}, "redirect_url": "https://a/9",
                         "salary_min": 600000, "salary_max": 900000}]}
    (j,) = adzuna.parse(data, "in")
    assert (j.salary_min, j.salary_currency) == (600000, "INR")
