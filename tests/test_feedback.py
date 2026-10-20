"""Dismiss reasons + learning, hiding staffing agencies, hiding old/possibly-closed postings, skills-gap insight."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import agencies, insights
from app.main import create_app
from app.matcher import score_job
from app.pipeline import rescore
from app.store import Store
from tests.conftest import make_job


def iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def ids(store, **kw):
    return {j["company"] + "|" + j["title"] for j in store.query_jobs(limit=200, **kw)["items"]}


def jid(store, company):
    return next(j["id"] for j in store.query_jobs(limit=200, include_closed=True, statuses=["new", "saved", "applied", "dismissed"])["items"] if j["company"] == company)


def touch(store, company, **cols):
    """Directly set columns (e.g. last_seen) the way a crawl would have left them."""
    with store.conn() as c:
        for k, v in cols.items():
            c.execute(f"UPDATE jobs SET {k}=? WHERE company=?", (v, company))


# ====================================================================================== agencies: the detector
@pytest.mark.parametrize("company,text", [
    ("Saral Recruitments", ""), ("Acme Staffing Solutions", ""), ("Apex Recruiters", ""), ("Global Outsourcing Ltd", ""),
    ("Hays", ""), ("Kelly Services India", ""), ("TeamLease Digital", ""), ("PradeepIT Consulting Services Pvt Ltd", ""),
    ("Whatever Ltd", "Our client, a leading MNC, is looking for a Java developer on a C2H basis"),
    ("Whatever Ltd", "We are an Indian recruitment agency hiring for several clients."),
    ("Whatever Ltd", "This is a contract-to-hire role on the payroll of the consultancy."),
])
def test_agencies_are_recognised(company, text):
    assert agencies.is_agency(company, text) and agencies.agency_reason(company, text)


@pytest.mark.parametrize("company,text", [
    ("Stripe", "We build payments."), ("Recruiterflow", "Recruiting software."), ("Recruitee", ""), ("Haystack Analytics", ""),
    ("Placement Labs", ""), ("Zoho", "We are hiring for our team."),
    ("Bar", "We are hiring for a client-facing role in our team."),                               # 'client' alone is not enough
    ("WhatBytes", "Sister brands\n\nHYRMUS\n\nHyrmus Services LLP is an Indian recruitment agency founded in 2023"),   # mentions one, is not one
    ("", ""),
])
def test_real_employers_are_not_flagged(company, text):
    assert not agencies.is_agency(company, text)


def test_new_jobs_are_flagged_at_insert_and_the_reason_is_returned(store):
    store.upsert_jobs([make_job(company="Saral Recruitments", external_id="1"), make_job(company="Stripe", external_id="2")])
    by = {j["company"]: j for j in store.query_jobs()["items"]}
    assert by["Saral Recruitments"]["agency"] and "name" in by["Saral Recruitments"]["agency"]
    assert by["Stripe"]["agency"] == ""
    assert "agency_reason" not in by["Stripe"]                                                     # internal column name is not leaked


def test_an_old_database_is_upgraded_and_existing_agencies_are_flagged(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL UNIQUE, source TEXT NOT NULL,
        external_id TEXT NOT NULL, board TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, company TEXT NOT NULL, location TEXT NOT NULL DEFAULT '',
        remote INTEGER NOT NULL DEFAULT 0, is_india INTEGER NOT NULL DEFAULT 0, url TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
        posted_at TEXT, salary TEXT NOT NULL DEFAULT '', salary_min_lpa REAL, salary_max_lpa REAL, salary_currency TEXT NOT NULL DEFAULT '',
        department TEXT NOT NULL DEFAULT '', employment_type TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]',
        level TEXT NOT NULL DEFAULT 'unknown', score INTEGER NOT NULL DEFAULT 0, matched TEXT NOT NULL DEFAULT '[]',
        reasons TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'applied', closed INTEGER NOT NULL DEFAULT 0,
        first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
        INSERT INTO jobs(fingerprint,source,external_id,title,company,url,first_seen,last_seen) VALUES
        ('a','greenhouse','1','Dev','Saral Recruitments','http://x/1','2026-01-01','2026-01-01'),
        ('b','greenhouse','2','Dev','Stripe','http://x/2','2026-01-01','2026-01-01');""")
    db.commit(); db.close()
    s = Store(path)                                                                                 # opening it runs the migration
    by = {j["company"]: j for j in s.query_jobs(statuses=["applied"])["items"]}
    assert by["Saral Recruitments"]["agency"] and by["Stripe"]["agency"] == ""
    assert by["Stripe"]["status"] == "applied"                                                      # nothing else was touched
    Store(path)                                                                                     # opening again is harmless


def test_hide_agencies_hides_them_only_from_the_active_list_and_counts_them(store):
    store.upsert_jobs([make_job(company="Saral Recruitments", external_id="1"), make_job(company="Stripe", external_id="2"),
                       make_job(company="Hays", external_id="3")])
    r = store.query_jobs(hide_agencies=True)
    assert [j["company"] for j in r["items"]] == ["Stripe"] and r["total"] == 1 and r["hidden"] == {"old": 0, "agencies": 2}
    assert store.query_jobs()["total"] == 3 and store.query_jobs()["hidden"] == {"old": 0, "agencies": 0}      # off by default
    store.set_status(jid(store, "Hays"), "saved")
    assert ids(store, statuses=["saved"], hide_agencies=True) == {"Hays|Software Engineer"}         # an explicit tab is never filtered
    assert ids(store, hide_agencies=True) == {"Stripe|Software Engineer", "Hays|Software Engineer"}  # ...and a job you SAVED is never tidied away
    assert store.query_jobs(hide_agencies=True)["hidden"]["agencies"] == 1                           # only the unsaved agency job is hidden


def test_hidden_counts_respect_the_other_filters(store):
    store.upsert_jobs([make_job(company="Hays", external_id="1", title="Frontend Developer"), make_job(company="Randstad", external_id="2", title="Backend Developer")])
    assert store.query_jobs(q="frontend", hide_agencies=True)["hidden"]["agencies"] == 1


# ====================================================================================== old / possibly closed postings
def test_old_postings_are_hidden_but_counted_and_undated_ones_use_first_seen(store):
    store.upsert_jobs([make_job(company="Fresh", external_id="1", posted_at=iso(3)), make_job(company="Ancient", external_id="2", posted_at=iso(200)),
                       make_job(company="Undated", external_id="3", posted_at=None)])
    touch(store, "Fresh", first_seen=iso(3)); touch(store, "Ancient", first_seen=iso(200)); touch(store, "Undated", first_seen=iso(100))
    r = store.query_jobs(hide_old=True)
    assert {j["company"] for j in r["items"]} == {"Fresh"} and r["hidden"]["old"] == 2
    assert {j["company"] for j in store.query_jobs()["items"]} == {"Fresh", "Ancient", "Undated"}
    touch(store, "Undated", first_seen=iso(10))
    assert {j["company"] for j in store.query_jobs(hide_old=True)["items"]} == {"Fresh", "Undated"}


def test_possibly_closed_means_not_seen_for_a_week_compared_with_the_newest_sighting(store):
    store.upsert_jobs([make_job(company="Seen", external_id="1", posted_at=iso(2)), make_job(company="Vanished", external_id="2", posted_at=iso(2))])
    touch(store, "Seen", last_seen=iso(0.1)); touch(store, "Vanished", last_seen=iso(9))
    assert {j["company"] for j in store.query_jobs(hide_old=True)["items"]} == {"Seen"}


def test_skipping_crawls_does_not_make_everything_look_closed(store):
    """No crawl for a month: every job is 'old' in absolute terms, but none has vanished relative to the others."""
    store.upsert_jobs([make_job(company="A", external_id="1", posted_at=iso(5)), make_job(company="B", external_id="2", posted_at=iso(5))])
    touch(store, "A", last_seen=iso(30)); touch(store, "B", last_seen=iso(30.5))
    assert store.query_jobs(hide_old=True)["total"] == 2


def test_hide_rules_leave_applied_and_dismissed_tabs_alone(store):
    store.upsert_jobs([make_job(company="Ancient", external_id="1", posted_at=iso(300))])
    touch(store, "Ancient", first_seen=iso(300))
    store.set_status(jid(store, "Ancient"), "applied")
    assert store.query_jobs(statuses=["applied"], hide_old=True)["total"] == 1


# ====================================================================================== dismiss reasons
def test_a_dismissal_can_carry_a_reason_and_it_is_forgotten_on_restore(store):
    store.upsert_jobs([make_job()])
    i = store.query_jobs()["items"][0]["id"]
    assert store.set_status(i, "dismissed", "too_senior")
    assert store.get_job(i)["dismiss_reason"] == "too_senior"
    assert store.set_status(i, "new") and store.get_job(i)["dismiss_reason"] == ""
    assert store.set_status(i, "dismissed") and store.get_job(i)["dismiss_reason"] == ""             # no reason is fine


@pytest.mark.parametrize("status,reason", [("saved", "location"), ("applied", "salary"), ("new", "other"), ("dismissed", "bogus")])
def test_bad_reasons_are_rejected(store, status, reason):
    store.upsert_jobs([make_job()])
    with pytest.raises(ValueError):
        store.set_status(store.query_jobs()["items"][0]["id"], status, reason)


def test_unknown_job_returns_false(store):
    assert store.set_status(999, "dismissed", "other") is False


def test_hide_company_blocks_every_job_of_that_company_in_the_active_list_only(store):
    store.upsert_jobs([make_job(company="Annoying Inc", external_id="1", title="A"), make_job(company="annoying inc ", external_id="2", title="B"),
                       make_job(company="Fine Co", external_id="3", title="C")])
    first = jid(store, "Annoying Inc")
    store.set_status(first, "dismissed", "company")
    assert ids(store) == {"Fine Co|C"}                                                               # the sibling job is hidden too (case/space-insensitive)
    assert store.company_flags() == {"blocked": ["Annoying Inc"], "agencies": []}
    saved = jid(store, "annoying inc")
    store.set_status(saved, "saved")
    assert "annoying inc|B" in ids(store, statuses=["saved"])                                       # explicit tabs still show what you saved
    assert store.stats()["strong_matches"] == store.query_jobs(min_score=60)["total"]               # counts agree with the list


def test_restoring_the_job_unblocks_the_company(store):
    store.upsert_jobs([make_job(company="Annoying Inc", external_id="1", title="A"), make_job(company="Annoying Inc", external_id="2", title="B")])
    first = jid(store, "Annoying Inc")
    store.set_status(first, "dismissed", "company")
    assert store.query_jobs()["total"] == 0                                                          # both jobs are gone from the Active list
    store.set_status(first, "new")
    assert store.company_flags()["blocked"] == [] and store.query_jobs()["total"] == 2


def test_changing_the_reason_swaps_the_flag(store):
    store.upsert_jobs([make_job(company="Hmm Ltd")])
    i = jid(store, "Hmm Ltd")
    store.set_status(i, "dismissed", "company")
    store.set_status(i, "dismissed", "agency")
    assert store.company_flags() == {"blocked": [], "agencies": ["Hmm Ltd"]}
    store.set_status(i, "dismissed", "other")
    assert store.company_flags() == {"blocked": [], "agencies": []}


def test_dismissing_as_agency_flags_the_company_for_the_hide_agencies_switch(store):
    store.upsert_jobs([make_job(company="Sneaky Talent", external_id="1", title="A"), make_job(company="Sneaky Talent", external_id="2", title="B"),
                       make_job(company="Real Co", external_id="3", title="C")])
    store.set_status(jid(store, "Sneaky Talent"), "dismissed", "agency")
    assert ids(store, hide_agencies=True) == {"Real Co|C"}
    assert len(ids(store)) == 2                                                                      # without the switch it is still listed
    assert store.query_jobs()["items"][0]["agency"] or store.query_jobs()["items"][1]["agency"]


def test_manual_flags_validate_and_are_idempotent(store):
    store.flag_company("Foo", "blocked"); store.flag_company("FOO ", "blocked"); store.flag_company("Foo", "agency")
    assert store.company_flags() == {"blocked": ["Foo"], "agencies": ["Foo"]}
    assert store.unflag_company("foo", "blocked") and not store.unflag_company("foo", "blocked")
    for bad in (("", "blocked"), ("  ", "agency"), ("x", "weird")):
        with pytest.raises(ValueError):
            store.flag_company(*bad)


def test_stats_follow_the_hide_rules(store, profile):
    store.upsert_jobs([make_job(company="Hays", external_id="1", description="React Node.js Express MongoDB"),
                       make_job(company="Stripe", external_id="2", description="React Node.js Express MongoDB")])
    rescore(store, profile)
    assert store.stats()["strong_matches"] == 2 and store.stats(hide_agencies=True)["strong_matches"] == 1
    assert store.stats(hide_agencies=True)["salary_buckets"]["none"] == 1


# ====================================================================================== learning from "wrong tech stack"
def _wrong_stack(store, n, skills="Django Python"):
    store.upsert_jobs([make_job(company=f"Py{i}", external_id=f"py{i}", title=f"Backend Engineer {i}", description=f"{skills} PostgreSQL") for i in range(n)])
    for i in range(n):
        store.set_status(jid(store, f"Py{i}"), "dismissed", "wrong_stack")


def test_technologies_in_two_wrong_stack_dismissals_become_avoided(store):
    _wrong_stack(store, 1, "Django")
    assert store.learned_avoid_skills() == []                                                       # one job proves nothing
    _wrong_stack(store.__class__(store.path), 0)
    store.upsert_jobs([make_job(company="Py9", external_id="py9", title="Backend Engineer 9", description="Django Flask")])
    store.set_status(jid(store, "Py9"), "dismissed", "wrong_stack")
    assert "django" in store.learned_avoid_skills() and "flask" not in store.learned_avoid_skills()


def test_generic_skills_and_other_reasons_do_not_teach_anything(store):
    store.upsert_jobs([make_job(company=f"G{i}", external_id=f"g{i}", title=f"Dev {i}", description="Git REST API HTML CSS Docker") for i in range(3)])
    for i in range(3):
        store.set_status(jid(store, f"G{i}"), "dismissed", "too_senior")
    assert store.learned_avoid_skills() == []                                                       # wrong reason
    for i in range(3):
        store.set_status(jid(store, f"G{i}"), "dismissed", "wrong_stack")
    assert store.learned_avoid_skills() == ["docker"]                                               # git / rest / api / html / css are too generic


def test_restoring_a_job_takes_its_influence_away(store):
    _wrong_stack(store, 2)
    assert "django" in store.learned_avoid_skills()
    store.set_status(jid(store, "Py0"), "new")
    assert "django" not in store.learned_avoid_skills()


def test_avoided_technologies_lower_the_score_and_say_why(profile):
    job = dict(title="Backend Engineer", description="Django Python Docker React Node.js", is_india=True, remote=False)
    plain = score_job(**job, profile=profile)
    taught = score_job(**job, profile={**profile, "avoid_skills": ["django", "docker"]})
    assert taught.avoided == ["django", "docker"] and taught.avoid_penalty == 12
    assert taught.score == plain.score - 12 and any("passed on" in r and "django" in r for r in taught.reasons)
    assert score_job(**job, profile={**profile, "avoid_skills": ["django", "docker", "kafka", "rust"]}).avoid_penalty == 12   # only technologies the job uses
    many = "Django Docker Kubernetes Flask Spring"
    assert score_job(title="Backend Engineer", description=many, is_india=True, remote=False,
                     profile={**profile, "avoid_skills": ["django", "docker", "kubernetes", "flask", "spring"]}).avoid_penalty == 15   # capped
    assert plain.avoided == [] and plain.avoid_penalty == 0


def test_a_skill_you_list_yourself_is_never_penalised(profile):
    mine = {**profile, "avoid_skills": ["react"]}
    assert "react" in profile["skills"]
    assert score_job("Frontend Developer", "React", is_india=True, remote=False, profile=mine).avoid_penalty == 0


def test_rescore_applies_what_was_learned(store, profile):
    store.save_profile(profile)
    _wrong_stack(store, 2, "Django Python")
    store.upsert_jobs([make_job(company="Next", external_id="n1", title="Backend Engineer", description="Django Python React Node.js")])
    rescore(store, profile)
    after = next(j for j in store.query_jobs(limit=50)["items"] if j["company"] == "Next")
    assert any("passed on" in r for r in after["reasons"])


# ====================================================================================== skills gap insight
def _row(title="Dev", desc="", tags="[]", dept=""):
    return {"title": title, "description": desc, "tags": tags, "department": dept}


def test_skill_gaps_count_jobs_not_mentions(profile):
    rows = [_row(desc="TypeScript TypeScript TypeScript Docker React"), _row(desc="TypeScript AWS React"), _row(desc="Docker AWS Node.js"),
            _row(desc="Kubernetes")]
    out = insights.skill_gaps(rows, profile)
    gaps = {g["skill"]: g for g in out["gaps"]}
    assert out["jobs"] == 4 and gaps["typescript"]["jobs"] == 2 and gaps["docker"]["jobs"] == 2 and gaps["aws"]["jobs"] == 2
    assert "kubernetes" not in gaps                                                                  # one job only: not worth mentioning
    assert gaps["typescript"]["share"] == 0.5
    assert [g["skill"] for g in out["gaps"]] == sorted(gaps, key=lambda s: (-gaps[s]["jobs"], s))     # most wanted first, ties alphabetical


def test_skill_gaps_exclude_what_you_have_and_generic_skills_and_show_what_you_have(profile):
    rows = [_row(desc="React Node.js Git REST API HTML Docker"), _row(desc="React Docker Git HTML")]
    out = insights.skill_gaps(rows, profile)
    assert {g["skill"] for g in out["gaps"]} == {"docker"}
    assert {h["skill"]: h["jobs"] for h in out["have"]} == {"react": 2, "node": 1} or {h["skill"] for h in out["have"]} >= {"react"}
    assert all(h["skill"] not in ("git", "html", "api", "rest") for h in out["have"])


def test_skill_gaps_mention_what_you_know_that_is_next_door(profile):
    rows = [_row(desc="TypeScript"), _row(desc="TypeScript")]
    (gap,) = [g for g in insights.skill_gaps(rows, profile)["gaps"] if g["skill"] == "typescript"]
    assert "javascript" in gap["close_to"]


def test_skill_gaps_with_no_jobs_or_no_profile():
    assert insights.skill_gaps([], {"skills": ["react"]}) == {"jobs": 0, "gaps": [], "have": []}
    assert insights.skill_gaps([_row(desc="Docker Docker")], None)["jobs"] == 1


def test_insight_uses_only_the_best_active_jobs(store, profile):
    store.save_profile(profile)
    store.upsert_jobs([make_job(company="Good", external_id="1", title="Full Stack Developer (0-2 years)", description="React Node.js Express MongoDB TypeScript Docker"),
                       make_job(company="Good2", external_id="2", title="Full Stack Developer (0-2 years)", description="React Node.js Express MongoDB TypeScript Docker"),
                       make_job(company="Hays", external_id="3", title="Full Stack Developer (0-2 years)", description="React Node.js Express MongoDB Kafka Kafka"),
                       make_job(company="Senior Co", external_id="4", title="Senior Staff Engineer", description="Rust Rust Go Go")])
    rescore(store, profile)
    rows = store.jobs_for_insight(min_score=50)
    assert {r["title"] for r in rows} == {"Full Stack Developer (0-2 years)"}                       # the capped senior job is not "a best match"
    assert len(rows) == 2                                                                            # Good + Good2; the agency's (Hays) job is left out
    assert len(store.jobs_for_insight(min_score=0)) == 3                                             # lowering the bar lets the senior one in, still not Hays


# ====================================================================================== the API
@pytest.fixture()
def api(store, profile):
    store.save_profile(profile)
    store.upsert_jobs([
        make_job(company="Saral Recruitments", external_id="1", title="Full Stack Developer (0-2 years)", description="React Node.js Express MongoDB TypeScript", posted_at=iso(1)),
        make_job(company="Alpha", external_id="2", title="Full Stack Developer (0-2 years)", description="React Node.js Express MongoDB TypeScript", posted_at=iso(1)),
        make_job(company="Old Co", external_id="3", title="Full Stack Developer (0-2 years)", description="React Node.js Express MongoDB", posted_at=iso(300)),
    ])
    touch(store, "Old Co", first_seen=iso(300))
    rescore(store, profile)
    return TestClient(create_app(store, crawler=lambda s: {})), store


def test_api_hide_flags_and_hidden_counts(api):
    client, _ = api
    assert client.get("/api/jobs?min_score=0").json()["total"] == 3
    r = client.get("/api/jobs?min_score=0&hide_old=true&hide_agencies=true").json()
    assert [j["company"] for j in r["items"]] == ["Alpha"] and r["hidden"] == {"old": 1, "agencies": 1}
    assert r["total"] == 1
    assert client.get("/api/jobs?min_score=0&hide_old=maybe").status_code == 422
    stats = client.get("/api/stats?hide_old=true&hide_agencies=true").json()
    assert stats["strong_matches"] == 1 and client.get("/api/stats").json()["strong_matches"] == 3


def test_api_status_with_reason_and_validation(api):
    client, store = api
    i = jid(store, "Alpha")
    assert client.post(f"/api/jobs/{i}/status", json={"status": "dismissed", "reason": "salary"}).json() == {"id": i, "status": "dismissed", "reason": "salary"}
    job = client.get(f"/api/jobs/{i}").json()
    assert job["dismiss_reason"] == "salary" and "agency" in job
    assert client.post(f"/api/jobs/{i}/status", json={"status": "saved", "reason": "salary"}).status_code == 422       # reason only with a dismissal
    assert client.post(f"/api/jobs/{i}/status", json={"status": "dismissed", "reason": "because"}).status_code == 422
    assert client.post("/api/jobs/9999/status", json={"status": "dismissed", "reason": "other"}).status_code == 404
    listed = client.get("/api/jobs?status=dismissed&min_score=0").json()["items"]
    assert listed[0]["dismiss_reason"] == "salary"


def test_api_hide_company_via_reason_and_the_flags_endpoints(api):
    client, store = api
    assert client.post(f"/api/jobs/{jid(store, 'Alpha')}/status", json={"status": "dismissed", "reason": "company"}).status_code == 200
    assert client.get("/api/companies/flags").json() == {"blocked": ["Alpha"], "agencies": []}
    assert client.post("/api/companies/flags", json={"company": "Beta Inc", "kind": "agency"}).status_code == 201
    assert client.post("/api/companies/flags", json={"company": "", "kind": "agency"}).status_code == 422
    assert client.post("/api/companies/flags", json={"company": "x", "kind": "nope"}).status_code == 422
    assert client.delete("/api/companies/flags", params={"company": "alpha", "kind": "blocked"}).json()["blocked"] == []
    assert client.delete("/api/companies/flags", params={"company": "alpha", "kind": "blocked"}).status_code == 404
    assert client.get("/api/jobs?min_score=0").json()["total"] == 2                                  # Alpha was dismissed, and is no longer blocked


def test_api_learning_rescores_only_when_the_avoid_list_changes(api):
    client, store = api
    store.upsert_jobs([make_job(company=f"W{i}", external_id=f"w{i}", title=f"Dev {i}", description="Django Flask") for i in range(2)])
    first, second = jid(store, "W0"), jid(store, "W1")
    r1 = client.post(f"/api/jobs/{first}/status", json={"status": "dismissed", "reason": "wrong_stack"}).json()
    assert "rescored" not in r1                                                                      # one dismissal: nothing learned yet
    r2 = client.post(f"/api/jobs/{second}/status", json={"status": "dismissed", "reason": "wrong_stack"}).json()
    assert r2["rescored"] > 0                                                                        # now 'django' and 'flask' are avoided
    detail = client.get(f"/api/jobs/{jid(store, 'Alpha')}").json()
    assert detail["breakdown"]["avoided"] == [] and detail["breakdown"]["avoid_points"] == 0
    r3 = client.post(f"/api/jobs/{second}/status", json={"status": "new"}).json()
    assert r3["rescored"] > 0                                                                        # restoring takes the lesson back


def test_api_breakdown_shows_avoided_technologies(api):
    client, store = api
    store.upsert_jobs([make_job(company=f"W{i}", external_id=f"w{i}", title=f"Dev {i}", description="Django Flask") for i in range(2)]
                      + [make_job(company="Target", external_id="t", title="Backend Engineer", description="Django Python React Node.js")])
    for i in range(2):
        client.post(f"/api/jobs/{jid(store, f'W{i}')}/status", json={"status": "dismissed", "reason": "wrong_stack"})
    b = client.get(f"/api/jobs/{jid(store, 'Target')}").json()["breakdown"]
    assert b["avoided"] == ["django"] and b["avoid_points"] == 6 and "avoided" not in b["parts"]


def test_api_skill_gaps(api):
    client, _ = api
    r = client.get("/api/insights/skills").json()
    assert r["min_score"] == 50 and r["jobs"] == 1                                                   # Alpha only: the agency and the old job are not counted
    assert client.get("/api/insights/skills?min_score=0").json()["jobs"] >= 1
    assert client.get("/api/insights/skills?min_score=101").status_code == 422


def test_api_skill_gaps_need_a_profile(tmp_path):
    client = TestClient(create_app(Store(tmp_path / "empty.db"), crawler=lambda s: {}))
    assert client.get("/api/insights/skills").status_code == 404


# ====================================================================================== the flags every job carries
def test_each_job_says_whether_it_is_old_or_possibly_closed(store):
    store.upsert_jobs([make_job(company="Fresh", external_id="1", posted_at=iso(3)), make_job(company="Ancient", external_id="2", posted_at=iso(200)),
                       make_job(company="Vanished", external_id="3", posted_at=iso(3))])
    touch(store, "Ancient", first_seen=iso(200)); touch(store, "Vanished", last_seen=iso(20))
    by = {j["company"]: j for j in store.query_jobs()["items"]}
    assert (by["Fresh"]["old"], by["Fresh"]["possibly_closed"]) == (False, False)
    assert (by["Ancient"]["old"], by["Ancient"]["possibly_closed"]) == (True, False)
    assert (by["Vanished"]["old"], by["Vanished"]["possibly_closed"]) == (False, True)
    detail = store.get_job(jid(store, "Ancient"))
    assert detail["old"] is True and "possibly_closed" in detail


def test_the_flags_are_shown_even_when_the_hide_rules_are_off_and_on_other_tabs(store):
    store.upsert_jobs([make_job(company="Ancient", external_id="2", posted_at=iso(200))])
    touch(store, "Ancient", first_seen=iso(200))
    store.set_status(jid(store, "Ancient"), "saved")
    assert store.query_jobs(statuses=["saved"])["items"][0]["old"] is True                         # a saved job still tells you it is stale


# ====================================================================================== experience_conflict reaches the API
def test_the_api_flags_a_posting_that_contradicts_itself(store, profile):
    from tests.test_matcher import KSHM
    store.save_profile(profile)
    store.upsert_jobs([make_job(company="Kshm", external_id="k", title="Full Stack Developer", description=KSHM),
                       make_job(company="Plain", external_id="p", title="Full Stack Developer", description="Experience: 3 years of exp.\n\nReact Node.js")])
    rescore(store, profile)
    client = TestClient(create_app(store, crawler=lambda s: {}))
    by = {j["company"]: j for j in client.get("/api/jobs?min_score=0").json()["items"]}
    assert by["Kshm"]["exp_conflict"] == {"header": 2, "text": 3} and by["Kshm"]["level"] == "unknown"
    assert by["Plain"]["exp_conflict"] is None and by["Plain"]["level"] == "mid"
    assert client.get(f"/api/jobs/{by['Kshm']['id']}").json()["exp_conflict"] == {"header": 2, "text": 3}
