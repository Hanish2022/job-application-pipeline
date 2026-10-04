import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.pipeline import rescore
from tests.conftest import RESUME_TEXT, make_job


@pytest.fixture()
def seeded(store, profile):
    store.save_profile(profile)
    store.upsert_jobs([
        make_job(external_id="1", title="Full Stack Developer (0-2 years)", company="Alpha", description="React Node.js Express MongoDB"),
        make_job(external_id="2", title="Senior Backend Engineer", company="Beta", description="Java 8+ years of experience", location="Pune"),
        make_job(external_id="3", title="Frontend Intern", company="Gamma", description="React CSS", source="lever", location="Remote", remote=True),
    ])
    rescore(store, profile)
    return store


@pytest.fixture()
def client(seeded):
    return TestClient(create_app(seeded, crawler=lambda s: {}))


def test_health_and_index(client):
    assert client.get("/api/health").json() == {"ok": True}
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"] and "Jobs" in r.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


def test_list_jobs_default_and_filters(client):
    data = client.get("/api/jobs").json()
    assert data["total"] == 3 and data["items"][0]["title"].startswith("Full Stack")
    assert data["items"][0]["score"] >= data["items"][1]["score"] >= data["items"][2]["score"]
    assert client.get("/api/jobs?level=senior").json()["total"] == 1
    assert client.get("/api/jobs?level=intern,entry").json()["total"] == 2
    assert client.get("/api/jobs?source=lever").json()["total"] == 1
    assert client.get("/api/jobs?location=remote").json()["total"] == 1
    assert client.get("/api/jobs?q=mongodb").json()["total"] == 1
    assert client.get("/api/jobs?company=gam").json()["total"] == 1
    assert client.get("/api/jobs?min_score=90").json()["total"] >= 1
    assert client.get("/api/jobs?limit=1&offset=1").json()["items"][0]["title"] != data["items"][0]["title"]


@pytest.mark.parametrize("qs", ["level=bogus", "location=mars", "status=nope", "sort=random", "min_score=101", "limit=0", "limit=201", "offset=-1", "days=0"])
def test_list_jobs_rejects_bad_input(client, qs):
    assert client.get(f"/api/jobs?{qs}").status_code == 422


def test_job_detail(client):
    jid = client.get("/api/jobs").json()["items"][0]["id"]
    job = client.get(f"/api/jobs/{jid}").json()
    assert "description" in job and "fingerprint" not in job and job["reasons"]
    assert client.get("/api/jobs/99999").status_code == 404


def test_status_lifecycle_and_tabs(client):
    jid = client.get("/api/jobs").json()["items"][0]["id"]
    assert client.post(f"/api/jobs/{jid}/status", json={"status": "saved"}).json() == {"id": jid, "status": "saved"}
    assert client.get("/api/jobs?status=saved").json()["total"] == 1
    client.post(f"/api/jobs/{jid}/status", json={"status": "dismissed"})
    assert client.get("/api/jobs").json()["total"] == 2                      # hidden from the default list
    assert client.get("/api/jobs?status=dismissed").json()["total"] == 1
    client.post(f"/api/jobs/{jid}/status", json={"status": "applied"})
    stats = client.get("/api/stats").json()
    assert stats["by_status"]["applied"] == 1 and stats["crawling"] is False
    assert client.post(f"/api/jobs/{jid}/status", json={"status": "bogus"}).status_code == 422
    assert client.post("/api/jobs/99999/status", json={"status": "saved"}).status_code == 404


def test_profile_get_and_update_rescoring(client):
    p = client.get("/api/profile").json()
    assert "react" in p["skills"]
    before = client.get("/api/jobs?q=intern").json()["items"][0]["score"]
    res = client.put("/api/profile", json={"exclude_keywords": ["intern"], "locations": ["india"], "skills": ["React ", "react", "Svelte"]})
    assert res.status_code == 200 and res.json()["rescored"] == 3
    prof = res.json()["profile"]
    assert prof["skills"] == ["react", "svelte"] and prof["exclude_keywords"] == ["intern"] and prof["locations"] == ["india"]
    assert client.get("/api/jobs?q=intern&min_score=0").json()["items"][0]["score"] < before


@pytest.mark.parametrize("body", [{"locations": ["mars"]}, {"level": "god"}, {"skills": "react"}, {"wants_internships": "maybe"}])
def test_profile_update_validation(client, body):
    assert client.put("/api/profile", json=body).status_code == 422


def test_profile_404_without_resume(store):
    c = TestClient(create_app(store, crawler=lambda s: {}))
    assert c.get("/api/profile").status_code == 404
    assert c.put("/api/profile", json={"level": "mid"}).status_code == 404


def test_resume_upload(store):
    c = TestClient(create_app(store, crawler=lambda s: {}))
    store.upsert_jobs([make_job(title="Full Stack Developer", description="React Node")])
    r = c.post("/api/profile/resume", files={"file": ("cv.txt", RESUME_TEXT.encode(), "text/plain")})
    assert r.status_code == 200 and r.json()["rescored"] == 1
    assert r.json()["profile"]["source_file"] == "cv.txt" and store.load_profile()["name"] == "Jane Doe"
    assert c.get("/api/jobs").json()["items"][0]["score"] > 0


def test_resume_upload_rejections(store):
    c = TestClient(create_app(store, crawler=lambda s: {}))
    assert c.post("/api/profile/resume", files={"file": ("cv.exe", b"MZ", "application/octet-stream")}).status_code == 415
    assert c.post("/api/profile/resume", files={"file": ("cv.txt", b"nothing useful here at all, just prose", "text/plain")}).status_code == 422
    assert c.post("/api/profile/resume", files={"file": ("cv.pdf", b"%PDF-1.4 garbage", "application/pdf")}).status_code == 422
    assert c.post("/api/profile/resume", files={"file": ("cv.txt", b"x" * (5 * 1024 * 1024 + 10), "text/plain")}).status_code == 413
    assert store.load_profile() is None


def test_crawl_runs_in_background_and_rejects_overlap(seeded):
    gate = threading.Event()
    called = []

    def crawler(s):
        called.append(1)
        gate.wait(5)

    c = TestClient(create_app(seeded, crawler=crawler))
    assert c.post("/api/crawl").status_code == 202
    assert c.get("/api/crawl/status").json()["running"] is True
    assert c.post("/api/crawl").status_code == 409
    gate.set()
    for _ in range(50):
        if not c.get("/api/crawl/status").json()["running"]:
            break
        time.sleep(0.05)
    assert c.get("/api/crawl/status").json()["running"] is False and called == [1]


def test_crawl_blocked_by_db_level_run(seeded):
    seeded.start_run()                      # e.g. the cron job is mid-run
    c = TestClient(create_app(seeded, crawler=lambda s: {}))
    assert c.post("/api/crawl").status_code == 409
    assert c.get("/api/stats").json()["crawling"] is True


def test_crawl_error_is_reported(seeded):
    def boom(s):
        raise RuntimeError("network down")

    c = TestClient(create_app(seeded, crawler=boom))
    c.post("/api/crawl")
    for _ in range(50):
        st = c.get("/api/crawl/status").json()
        if not st["running"]:
            break
        time.sleep(0.05)
    assert "network down" in st["error"]


def test_crawl_without_resume_or_profile_is_422(store, monkeypatch, tmp_path):
    monkeypatch.setattr("app.main.ensure_profile", lambda s: (_ for _ in ()).throw(__import__("app.resume", fromlist=["ResumeError"]).ResumeError("no file")))
    c = TestClient(create_app(store, crawler=lambda s: {}))
    assert c.post("/api/crawl").status_code == 422


def test_xss_payload_is_returned_as_data_not_markup(store, profile):
    store.save_profile(profile)
    store.upsert_jobs([make_job(title="<img src=x onerror=alert(1)> Dev", description="<script>alert(1)</script> React")])
    c = TestClient(create_app(store, crawler=lambda s: {}))
    item = c.get("/api/jobs").json()["items"][0]
    assert item["title"].startswith("<img")          # stored verbatim; the UI renders via textContent (covered by e2e)
    assert c.get(f"/api/jobs/{item['id']}").headers["content-type"].startswith("application/json")


def test_salary_fields_filters_and_sort(store, profile):
    store.save_profile(profile)
    store.upsert_jobs([
        make_job(external_id="1", title="Dev One", salary_min=1_500_000, salary_max=2_000_000, salary_currency="INR"),
        make_job(external_id="2", title="Dev Two", salary_min=90_000, salary_max=110_000, salary_currency="USD"),
        make_job(external_id="3", title="Dev Three"),
    ])
    c = TestClient(create_app(store, crawler=lambda s: {}))
    items = {j["title"]: j for j in c.get("/api/jobs").json()["items"]}
    assert items["Dev One"]["salary_lpa"] == "₹15–20 LPA" and items["Dev Two"]["salary_converted"] is True
    assert items["Dev Three"]["salary_lpa"] == ""
    assert {j["title"] for j in c.get("/api/jobs?min_lpa=50").json()["items"]} == {"Dev Two"}
    assert {j["title"] for j in c.get("/api/jobs?has_salary=true").json()["items"]} == {"Dev One", "Dev Two"}
    assert [j["title"] for j in c.get("/api/jobs?sort=salary").json()["items"]][:2] == ["Dev Two", "Dev One"]
    assert c.get("/api/jobs/" + str(items["Dev One"]["id"])).json()["salary_lpa"] == "₹15–20 LPA"
    assert c.get("/api/stats").json()["with_salary"] == 2
    for bad in ("min_lpa=-1", "min_lpa=1001", "min_lpa=abc", "has_salary=maybe"):
        assert c.get(f"/api/jobs?{bad}").status_code == 422


def test_default_list_is_active_only_new_and_saved_applied_and_dismissed_are_opt_in(seeded):
    c = TestClient(create_app(seeded, crawler=lambda s: {}))
    ids = {j["title"]: j["id"] for j in c.get("/api/jobs?min_score=0").json()["items"]}
    assert len(ids) == 3
    c.post(f"/api/jobs/{ids['Senior Backend Engineer']}/status", json={"status": "saved"})
    c.post(f"/api/jobs/{ids['Frontend Intern']}/status", json={"status": "applied"})
    c.post(f"/api/jobs/{ids['Full Stack Developer (0-2 years)']}/status", json={"status": "dismissed"})
    active = {j["title"] for j in c.get("/api/jobs?min_score=0").json()["items"]}
    assert active == {"Senior Backend Engineer"}                                        # saved stays; applied and dismissed leave
    assert {j["title"] for j in c.get("/api/jobs?status=applied&min_score=0").json()["items"]} == {"Frontend Intern"}
    assert {j["title"] for j in c.get("/api/jobs?status=dismissed&min_score=0").json()["items"]} == {"Full Stack Developer (0-2 years)"}
    both = {j["title"] for j in c.get("/api/jobs?status=saved,applied&min_score=0").json()["items"]}
    assert both == {"Senior Backend Engineer", "Frontend Intern"}
    s = c.get("/api/stats").json()
    assert s["by_status"] == {"saved": 1, "applied": 1, "dismissed": 1}                 # counts for the tabs still include everything
    assert s["strong_matches"] == 0                                                     # the only active job is a senior role (score 25)
    c.post(f"/api/jobs/{ids['Frontend Intern']}/status", json={"status": "new"})        # un-apply -> back in Active
    assert "Frontend Intern" in {j["title"] for j in c.get("/api/jobs?min_score=0").json()["items"]}


def test_job_detail_explains_the_score(client, seeded):
    items = {j["title"]: j for j in client.get("/api/jobs?min_score=0").json()["items"]}
    good = client.get(f"/api/jobs/{items['Full Stack Developer (0-2 years)']['id']}").json()
    b = good["breakdown"]
    assert set(b["parts"]) == {"skills", "role", "level", "location"} and b["cap"] is None
    assert b["total"] == good["score"] == round(sum(p["points"] for p in b["parts"].values()))        # the parts ARE the score
    assert {k: p["max"] for k, p in b["parts"].items()} == {"skills": 50, "role": 25, "level": 20, "location": 5}
    senior = client.get(f"/api/jobs/{items['Senior Backend Engineer']['id']}").json()["breakdown"]
    assert senior["cap"] == 25 and senior["total"] == 25 and sum(p["points"] for p in senior["parts"].values()) > 25


def test_job_detail_has_no_breakdown_without_a_profile(store):
    store.upsert_jobs([make_job()])
    c = TestClient(create_app(store, crawler=lambda s: {}))
    (item,) = c.get("/api/jobs?min_score=0").json()["items"]
    assert c.get(f"/api/jobs/{item['id']}").json()["breakdown"] is None


def test_salary_param_multi_select_and_validation(store, profile):
    store.save_profile(profile)
    store.upsert_jobs([
        make_job(external_id="1", title="Dev One", salary_min=1_500_000, salary_max=2_000_000, salary_currency="INR"),     # 15-20
        make_job(external_id="2", title="Dev Two", salary_min=90_000, salary_max=110_000, salary_currency="USD"),           # ≈79-97
        make_job(external_id="3", title="Dev Three"),
    ])
    c = TestClient(create_app(store, crawler=lambda s: {}))
    titles = lambda qs: {j["title"] for j in c.get("/api/jobs?min_score=0&" + qs).json()["items"]}   # noqa: E731
    assert titles("salary=15-25") == {"Dev One"}
    assert titles("salary=15-25,40-") == {"Dev One", "Dev Two"}
    assert titles("salary=15-25,none") == {"Dev One", "Dev Three"}
    assert titles("salary=") == {"Dev One", "Dev Two", "Dev Three"}
    assert titles("salary=15-25&has_salary=true&min_lpa=10") == {"Dev One"}
    assert c.get("/api/stats").json()["salary_buckets"]["15-25"] == 1
    for bad in ("5", "10-5", "x-y", "1-2;drop", "5-10,,oops", ",".join(f"{i}-{i + 1}" for i in range(20))):
        assert c.get("/api/jobs?salary=" + bad).status_code == 422, bad
