"""Deterministic server for the Playwright tests: seeded temp DB + a fake (offline) crawler.

usage: python tests/e2e/server.py <db-path> <port>
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402

from app import outreach  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Job  # noqa: E402
from app.pipeline import rescore  # noqa: E402
from app.resume import build_profile  # noqa: E402
from app.store import Store  # noqa: E402
from tests.conftest import RESUME_TEXT  # noqa: E402

XSS_TITLE = '<img src=x onerror="window.__xss=1"> Developer'


def iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def seed(store: Store, port: int) -> None:
    link = lambda n: f"http://127.0.0.1:{port}/api/health?job={n}"  # noqa: E731 - local, so popups need no network
    desc = "We build with React, Node.js, Express and MongoDB. REST APIs, Git."
    jobs = [
        Job("greenhouse", "1", "Full Stack Developer (0-2 years)", "Alpha", link(1), "Bengaluru, India", False, desc, iso(0.5), board="alpha",
            salary_min=1_200_000, salary_max=1_800_000, salary_currency="INR"),
        Job("lever", "2", "SDE I - Backend", "Beta", link(2), "Remote", True, desc, iso(60), board="beta",
            salary="USD 60,000–80,000 annual", salary_min=60_000, salary_max=80_000, salary_currency="USD"),
        Job("ashby", "3", "Frontend Intern", "Gamma", link(3), "Pune, India", False, "React, CSS, HTML. Internship for students.", iso(2), board="gamma",
            salary_min=25_000, salary_max=30_000, salary_currency="INR", salary_period="month"),
        Job("greenhouse", "4", "Senior Staff Engineer", "Delta", link(4), "Mumbai, India", False, "Java Kubernetes 10+ years of experience", iso(1), board="delta"),
        Job("greenhouse", "5", XSS_TITLE, "<b>Evil</b> Inc", "javascript:window.__xss=1", "Remote", True,
            "<script>window.__xss=1</script> React Node Express MongoDB", iso(3), board="evil"),
    ]
    # A search-only Wellfound result (details not fetched). Low score on purpose so default views stay unchanged.
    jobs.append(Job("wellfound", "9001", "Preview Only Tester", "Previewco", "https://wellfound.com/jobs/9001-preview-only-tester",
                    "Pune, India", False, "Manual testing", iso(4), board="wellfound", tags=["via-search"]))
    for i in range(35):
        jobs.append(Job("greenhouse", f"f{i}", f"Software Engineer {i:02d}", "Filler Co", link(100 + i), "Bengaluru, India", False, "React and Node.js", iso(5 + i * 0.1), board="filler"))
    store.upsert_jobs(jobs)
    profile = build_profile(RESUME_TEXT, "resume.txt")
    store.save_profile(profile)
    rescore(store, profile)
    rid = store.start_run()
    store.finish_run(rid, "ok", {"sources": {}})


def make_crawler(port: int):
    def crawler(store: Store) -> dict:
        rid = store.start_run()
        time.sleep(1.5)  # long enough for the UI to show its "Crawling…" state
        new = Job("greenhouse", "new1", "Freshly Crawled Engineer", "Newco", f"http://127.0.0.1:{port}/api/health?job=new", "Bengaluru, India", False,
                  "React Node.js Express MongoDB", iso(0), board="newco")
        res = store.upsert_jobs([new])
        rescore(store, store.load_profile())
        store.finish_run(rid, "ok", {"sources": {"greenhouse": {"inserted": res["inserted"]}}})
        return {}
    return crawler


def fake_yc_network() -> None:
    """Replace the vendored yc-outreach module's network with in-memory YC / Algolia / company-site responses."""
    import html as h
    import json

    vendor = outreach.load_vendor()
    batches = {"Winter 2024": 26, "Summer 2025": 2, "Unspecified": 1}

    def company(i: int) -> dict:
        name = "Evil<img src=x onerror=window.__xss=1>" if i == 0 else f"Co{i:02d}"
        return {"name": name, "slug": f"co{i}", "batch": "Winter 2024", "website": f"https://co{i}.example",
                "one_liner": f"Builds thing {i}.", "subindustry": "B2B", "team_size": 3, "launched_at": 1000 - i}

    def page(i: int) -> dict:
        return {"props": {"company": {"website": f"https://co{i}.example", "linkedin_url": "", "twitter_url": "",
                "founders": [{"full_name": f"Alice Founder{i}", "title": "CEO", "linkedin_url": "javascript:alert(1)" if i == 0 else "https://linkedin.com/in/alice",
                              "twitter_url": ""}]}}}

    def fake_get(url, timeout=8, data=None, headers=None):
        if url == "https://www.ycombinator.com/companies":
            return 'window.AlgoliaOpts = {"app":"APP","key":"KEY"}'
        if "algolia" in url:
            params = json.loads(data)["params"]
            if "facets" in params:
                return json.dumps({"facets": {"batch": batches}})
            if "Winter%202024" in params or "Winter+2024" in params:
                return json.dumps({"hits": [company(i) for i in range(26)], "nbPages": 1})
            return json.dumps({"hits": [dict(company(100 + i), batch="Summer 2025") for i in range(2)], "nbPages": 1})
        if url.startswith("https://www.ycombinator.com/companies/co"):
            return f'<div data-page="{h.escape(json.dumps(page(int(url.rsplit("/co", 1)[1]))))}"></div>'
        if url.startswith("https://co1.example"):
            return "Contact us: alice@co1.example or hello@co1.example"      # one company lists real addresses
        return None

    vendor.get = fake_get
    vendor._algolia = None
    vendor.resolves = lambda domain: True


if __name__ == "__main__":
    db, port = sys.argv[1], int(sys.argv[2])
    store = Store(db)
    seed(store, port)
    fake_yc_network()
    uvicorn.run(create_app(store, crawler=make_crawler(port)), host="127.0.0.1", port=port, log_level="warning")
