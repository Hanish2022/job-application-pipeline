import json

import pytest

from app.store import Store, fingerprint
from tests.conftest import make_job


def test_insert_and_dedupe_across_sources(store):
    a = make_job(source="greenhouse", external_id="1")
    b = make_job(source="lever", external_id="zzz", title="Software  Engineer!", url="https://other/1")  # same company/title/location
    assert store.upsert_jobs([a]) == {"inserted": 1, "updated": 0}
    assert store.upsert_jobs([b]) == {"inserted": 0, "updated": 1}
    assert store.query_jobs()["total"] == 1
    assert fingerprint(a) == fingerprint(b)


def test_different_location_is_a_different_job(store):
    store.upsert_jobs([make_job(location="Pune"), make_job(location="Mumbai", external_id="2")])
    assert store.query_jobs()["total"] == 2


def test_invalid_jobs_are_skipped(store):
    assert store.upsert_jobs([make_job(title=""), make_job(url=""), make_job(external_id="")])["inserted"] == 0


def test_none_fields_are_coerced(store):
    j = make_job(salary=None, department=None, description=None)
    assert store.upsert_jobs([j])["inserted"] == 1


def test_status_survives_recrawl(store):
    store.upsert_jobs([make_job()])
    jid = store.query_jobs()["items"][0]["id"]
    assert store.set_status(jid, "applied")
    store.upsert_jobs([make_job(description="updated text")])
    job = store.get_job(jid)
    assert job["status"] == "applied" and job["description"] == "updated text"


def test_set_status_validation(store):
    store.upsert_jobs([make_job()])
    jid = store.query_jobs()["items"][0]["id"]
    with pytest.raises(ValueError):
        store.set_status(jid, "bogus")
    assert store.set_status(99999, "saved") is False


def test_close_missing_and_reopen(store):
    jobs = [make_job(external_id=str(i), title=f"Dev {i}") for i in range(3)]
    store.upsert_jobs(jobs)
    seen = {fingerprint(j) for j in jobs[:2]}
    assert store.close_missing("greenhouse", "acme", seen) == 1
    assert store.query_jobs()["total"] == 2
    assert store.query_jobs(include_closed=True)["total"] == 3
    # Other boards are untouched
    store.upsert_jobs([make_job(board="other", title="Other", external_id="x")])
    assert store.close_missing("greenhouse", "acme", seen) == 0
    # A job that comes back is re-opened
    store.upsert_jobs([jobs[2]])
    assert store.query_jobs()["total"] == 4  # 3 acme jobs (one re-opened) + 1 from the other board


def _seed(store):
    rows = [
        make_job(external_id="1", title="Full Stack Developer", company="Alpha", location="Bengaluru, India", source="greenhouse", posted_at="2026-10-02T00:00:00+00:00"),
        make_job(external_id="2", title="Backend Engineer", company="Beta", location="Remote", source="lever", posted_at="2026-09-01T00:00:00+00:00", remote=True),
        make_job(external_id="3", title="Data Analyst", company="Gamma", location="Remote - US", source="ashby", posted_at="2026-08-01T00:00:00+00:00"),
        make_job(external_id="4", title="Frontend Intern", company="Alpha Labs", location="Pune", source="greenhouse", description="Figma CSS", posted_at=None),
    ]
    store.upsert_jobs(rows)
    by_title = {j["title"]: j["id"] for j in store.query_jobs(limit=50)["items"]}
    store.update_scores([
        (by_title["Full Stack Developer"], 90, "entry", ["react"], ["r"]),
        (by_title["Backend Engineer"], 60, "mid", ["node"], []),
        (by_title["Data Analyst"], 10, "unknown", [], []),
        (by_title["Frontend Intern"], 75, "intern", ["css"], []),
    ])
    return by_title


def test_query_filters_and_sorting(store):
    _seed(store)
    q = store.query_jobs
    assert [j["title"] for j in q()["items"]] == ["Full Stack Developer", "Frontend Intern", "Backend Engineer", "Data Analyst"]
    assert q(min_score=70)["total"] == 2
    assert {j["title"] for j in q(levels=["intern", "entry"])["items"]} == {"Full Stack Developer", "Frontend Intern"}
    assert {j["title"] for j in q(locations=["india"])["items"]} == {"Full Stack Developer", "Frontend Intern"}
    assert {j["title"] for j in q(locations=["remote"])["items"]} == {"Backend Engineer"}   # "Remote - US" is not eligible
    assert {j["title"] for j in q(locations=["india", "remote"])["items"]} == {"Full Stack Developer", "Frontend Intern", "Backend Engineer"}
    assert q(sources=["lever"])["total"] == 1
    assert q(company="alpha")["total"] == 2
    assert q(q="figma")["total"] == 1                       # matches description
    assert q(q="alpha intern")["total"] == 1                # every term must match
    assert q(q="nothing-matches")["total"] == 0
    assert [j["company"] for j in q(sort="company")["items"]][0] == "Alpha"
    assert q(sort="newest")["items"][0]["title"] in ("Full Stack Developer", "Frontend Intern")


def test_query_days_filter_falls_back_to_first_seen(store):
    _seed(store)
    # "Frontend Intern" has no posted_at -> uses first_seen (now) so it survives a 1-day window
    titles = {j["title"] for j in store.query_jobs(posted_within_days=1)["items"]}
    assert "Frontend Intern" in titles and "Data Analyst" not in titles


def test_query_pagination_and_bounds(store):
    store.upsert_jobs([make_job(external_id=str(i), title=f"Dev {i}") for i in range(25)])
    page1 = store.query_jobs(limit=10, offset=0)
    page3 = store.query_jobs(limit=10, offset=20)
    assert page1["total"] == 25 and len(page1["items"]) == 10 and len(page3["items"]) == 5
    assert len({j["id"] for j in page1["items"]} & {j["id"] for j in page3["items"]}) == 0
    assert store.query_jobs(limit=10_000)["limit"] == 200 and store.query_jobs(limit=0)["limit"] == 1


def test_dismissed_hidden_by_default_but_listable(store):
    ids = _seed(store)
    store.set_status(ids["Data Analyst"], "dismissed")
    assert store.query_jobs()["total"] == 3
    assert store.query_jobs(statuses=["dismissed"])["total"] == 1


def test_sql_injection_attempt_is_inert(store):
    _seed(store)
    assert store.query_jobs(q="'; DROP TABLE jobs; --")["total"] == 0
    assert store.query_jobs()["total"] == 4


def test_list_items_omit_description_but_have_snippet(store):
    store.upsert_jobs([make_job(description="x" * 1000)])
    item = store.query_jobs()["items"][0]
    assert "description" not in item and len(item["snippet"]) == 280
    assert len(store.get_job(item["id"])["description"]) == 1000


def test_stats(store):
    ids = _seed(store)
    store.set_status(ids["Full Stack Developer"], "saved")
    s = store.stats()
    assert s["total"] == 4 and s["strong_matches"] == 3 and s["by_status"]["saved"] == 1
    assert s["by_source"]["greenhouse"] == 2 and s["last_run"] is None


def test_profile_roundtrip(store):
    assert store.load_profile() is None
    store.save_profile({"skills": ["react"], "level": "entry"})
    store.save_profile({"skills": ["node"], "level": "entry"})
    assert store.load_profile()["skills"] == ["node"]


def test_runs_lifecycle_and_stale_detection(store):
    assert store.running_run() is None
    rid = store.start_run()
    assert store.running_run()["id"] == rid
    store.finish_run(rid, "ok", {"a": 1})
    assert store.running_run() is None
    assert store.recent_runs()[0]["stats"] == {"a": 1}
    # a run stuck in 'running' for > 2h is considered dead
    with store.conn() as c:
        c.execute("INSERT INTO runs(started_at, status) VALUES('2020-01-01T00:00:00+00:00','running')")
    assert store.running_run() is None


def test_persists_across_instances(tmp_path):
    p = tmp_path / "p.db"
    Store(p).upsert_jobs([make_job()])
    assert Store(p).query_jobs()["total"] == 1


# ------------------------------------------------------------------ salary (LPA)
def _salary_seed(store):
    store.upsert_jobs([
        make_job(external_id="a", title="Dev A", salary_min=1_200_000, salary_max=1_800_000, salary_currency="INR"),
        make_job(external_id="b", title="Dev B", salary_min=60_000, salary_max=80_000, salary_currency="USD"),
        make_job(external_id="c", title="Dev C", salary="₹6-9 LPA"),
        make_job(external_id="d", title="Dev D", description="Great perks. The salary range is $100,000 - $120,000 per year."),
        make_job(external_id="e", title="Dev E"),
    ])


def test_salary_is_normalised_at_ingest(store):
    _salary_seed(store)
    by = {j["title"]: j for j in store.query_jobs(limit=50)["items"]}
    assert by["Dev A"]["salary_lpa"] == "₹12–18 LPA" and by["Dev A"]["salary_converted"] is False
    assert by["Dev B"]["salary_lpa"] == "≈ ₹52.8–70.4 LPA" and by["Dev B"]["salary_converted"] is True
    assert by["Dev C"]["salary_lpa"] == "₹6–9 LPA"
    assert by["Dev D"]["salary_lpa"].startswith("≈ ₹88") and "100,000" in by["Dev D"]["salary"]   # found in the description
    assert by["Dev E"]["salary_lpa"] == "" and by["Dev E"]["salary_min_lpa"] is None


def test_min_lpa_filter_uses_top_of_range_and_excludes_unlisted(store):
    _salary_seed(store)
    titles = lambda **kw: {j["title"] for j in store.query_jobs(limit=50, **kw)["items"]}  # noqa: E731
    assert titles(min_lpa=10) == {"Dev A", "Dev B", "Dev D"}
    assert titles(min_lpa=15) == {"Dev A", "Dev B", "Dev D"}        # Dev A tops out at 18
    assert titles(min_lpa=60) == {"Dev B", "Dev D"}
    assert titles(min_lpa=200) == set()
    assert titles(has_salary=True) == {"Dev A", "Dev B", "Dev C", "Dev D"}
    assert "Dev E" in titles()


def test_sort_by_salary_puts_unlisted_last(store):
    _salary_seed(store)
    order = [j["title"] for j in store.query_jobs(sort="salary", limit=50)["items"]]
    assert order[:4] == ["Dev D", "Dev B", "Dev A", "Dev C"] and order[-1] == "Dev E"


def test_salary_refreshed_on_recrawl_and_counted_in_stats(store):
    store.upsert_jobs([make_job(title="Dev")])
    assert store.stats()["with_salary"] == 0
    store.upsert_jobs([make_job(title="Dev", salary_min=2_000_000, salary_max=3_000_000, salary_currency="INR")])
    assert store.stats()["with_salary"] == 1 and store.query_jobs()["items"][0]["salary_lpa"] == "₹20–30 LPA"


def test_old_database_is_migrated_and_backfilled(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL UNIQUE, source TEXT NOT NULL,
      external_id TEXT NOT NULL, board TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, company TEXT NOT NULL,
      location TEXT NOT NULL DEFAULT '', remote INTEGER NOT NULL DEFAULT 0, is_india INTEGER NOT NULL DEFAULT 0,
      url TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', posted_at TEXT, salary TEXT NOT NULL DEFAULT '',
      department TEXT NOT NULL DEFAULT '', employment_type TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]',
      level TEXT NOT NULL DEFAULT 'unknown', score INTEGER NOT NULL DEFAULT 0, matched TEXT NOT NULL DEFAULT '[]',
      reasons TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'new', closed INTEGER NOT NULL DEFAULT 0,
      first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
    INSERT INTO jobs (fingerprint,source,external_id,title,company,url,salary,description,first_seen,last_seen,status)
      VALUES ('f1','x','1','Old With Pay','Co','https://x/1','INR 1,800,000–2,200,000 annual','', '2026-01-01','2026-01-01','saved'),
             ('f2','x','2','Old Desc Pay','Co','https://x/2','','Our annual salary is ₹20-25 LPA.','2026-01-01','2026-01-01','new'),
             ('f3','x','3','Old No Pay','Co','https://x/3','','nothing','2026-01-01','2026-01-01','new');
    """)
    c.commit(); c.close()
    s = Store(path)
    by = {j["title"]: j for j in s.query_jobs(limit=10, statuses=["new", "saved"])["items"]}
    assert by["Old With Pay"]["salary_lpa"] == "₹18–22 LPA" and by["Old With Pay"]["status"] == "saved"   # data preserved
    assert by["Old Desc Pay"]["salary_lpa"] == "₹20–25 LPA" and by["Old No Pay"]["salary_lpa"] == ""
    Store(path)   # opening again is a no-op, not an error
    assert Store(path).stats()["with_salary"] == 2


# ------------------------------------------------------------- discovery-source helpers
def test_cross_source_duplicate_keeps_original_link_but_fills_pay(store):
    store.upsert_jobs([make_job(source="greenhouse", url="https://gh/1", description="original")])
    store.upsert_jobs([make_job(source="wellfound", url="https://wellfound.com/jobs/9", description="other",
                                salary_min=1_200_000, salary_max=1_800_000, salary_currency="INR")])
    (job,) = store.query_jobs()["items"]
    assert job["url"] == "https://gh/1" and job["source"] == "greenhouse"            # the apply link does not flip
    assert job["salary_lpa"] == "₹12–18 LPA"                                          # but the pay we lacked is filled in
    store.upsert_jobs([make_job(source="wellfound", url="https://w/9", salary_min=9_000_000, salary_max=9_900_000, salary_currency="INR")])
    assert store.query_jobs()["items"][0]["salary_lpa"] == "₹12–18 LPA"              # existing pay is not overwritten


def test_known_urls_touch_and_close_unseen(store):
    store.upsert_jobs([
        make_job(source="wellfound", external_id="1", title="Full", url="https://w/1"),
        make_job(source="wellfound", external_id="2", title="Preview", url="https://w/2", tags=["via-search"]),
        make_job(source="greenhouse", external_id="3", title="Other", url="https://g/3"),
    ])
    assert store.known_urls(["wellfound", "yc"]) == {"https://w/1": True, "https://w/2": False}
    with store.conn() as c:
        c.execute("UPDATE jobs SET last_seen='2020-01-01T00:00:00+00:00'")
    assert store.touch_urls(["https://w/1"]) == 1
    assert store.close_unseen(["wellfound", "yc"], 21) == 1                           # only the untouched wellfound job
    titles = {j["title"] for j in store.query_jobs(limit=10)["items"]}
    assert titles == {"Full", "Other"}                                                # greenhouse job unaffected
    store.touch_urls(["https://w/2"])                                                 # reappears in search => reopened
    assert "Preview" in {j["title"] for j in store.query_jobs(limit=10)["items"]}
