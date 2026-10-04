"""End-to-end tests of the dashboard in a real browser (Chromium via Playwright).

Seeded data (see server.py): 5 named jobs + 35 "Software Engineer NN" fillers; default view is Match >= 50.
"""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from app.config import APP_NAME  # noqa: E402

pytestmark = pytest.mark.e2e

JOBS = "[data-testid=job]"
TITLES = "[data-testid=job-title]"


def row(page, title):
    return page.locator(JOBS).filter(has=page.get_by_role("button", name=title, exact=True))


def open_profile(page):
    """Open the profile drawer and wait until it is actually usable: it loads the profile first, so interacting
    with its checkboxes before it is visible would race the form being filled in."""
    page.get_by_test_id("open-profile").click()
    expect(page.get_by_test_id("profile-drawer")).to_be_visible()


def level_box(page, name):
    return page.locator("#f-level").get_by_label(name)


def loc_box(page, name):
    return page.locator("#f-location").get_by_label(name, exact=True)


def titles(page):
    return page.locator(TITLES).all_inner_texts()


def wait_count(page, pattern):
    expect(page.get_by_test_id("count")).to_have_text(re.compile(pattern))


# ------------------------------------------------------------------ first load
def test_dashboard_loads_with_profile_stats_and_ranked_jobs(app):
    page = app
    expect(page).to_have_title(f"{APP_NAME} — jobs matched to your resume")
    expect(page.get_by_role("heading", level=1)).to_have_text("Jobs for Jane")
    stats = page.get_by_test_id("stats")
    expect(stats).to_contain_text("Open jobs tracked")
    expect(stats).to_contain_text("41")
    wait_count(page, r"Showing 30 of 39 jobs")
    scores = [int(s) for s in page.get_by_test_id("score").evaluate_all("els => els.map(e => e.firstChild.textContent)")]
    assert scores == sorted(scores, reverse=True) and scores[0] >= 85
    expect(page.locator("[data-testid=chip]")).to_have_text(re.compile(r"Match ≥ 50"))
    # The senior role is below the default threshold
    assert "Senior Staff Engineer" not in titles(page)
    expect(page.get_by_test_id("run-meta")).to_contain_text("Last refreshed")


def test_load_more_appends_the_rest(app):
    page = app
    expect(page.locator(JOBS)).to_have_count(30)
    page.get_by_test_id("load-more").click()
    expect(page.locator(JOBS)).to_have_count(39)
    wait_count(page, r"Showing 39 of 39")
    expect(page.get_by_test_id("load-more")).to_be_hidden()
    assert len(set(titles(page))) == 39            # no duplicates across pages


# --------------------------------------------------------------------- filters
def test_text_search_filters_and_updates_url(app):
    page = app
    page.get_by_test_id("search").fill("intern")
    wait_count(page, r"Showing 1 of 1 job$")
    assert titles(page) == ["Frontend Intern"]
    assert "q=intern" in page.url
    expect(page.locator("[data-testid=chip]").filter(has_text="“intern”")).to_be_visible()


def test_search_is_debounced_and_only_latest_result_wins(app):
    page = app
    box = page.get_by_test_id("search")
    box.press_sequentially("frontend", delay=30)      # fast typing: many keystrokes, few requests
    wait_count(page, r"Showing 1 of 1 job$")
    assert titles(page) == ["Frontend Intern"]


def test_match_score_segment_and_chip_removal(app):
    page = app
    page.get_by_role("radio", name="Any").click()
    wait_count(page, r"Showing 30 of 41 jobs")
    expect(page.locator("[data-testid=chip]")).to_have_count(0)
    page.get_by_role("radio", name="80+").click()
    expect(page.get_by_role("radio", name="80+")).to_have_attribute("aria-checked", "true")
    chip = page.locator("[data-testid=chip]").filter(has_text="Match ≥ 80")
    expect(chip).to_be_visible()
    wait_count(page, r"Showing \d+ of \d+ jobs?$")              # let the filtered results land before reading the number
    before = int(re.search(r"of (\d+)", page.get_by_test_id("count").inner_text()).group(1))
    chip.get_by_role("button").click()                      # remove the filter
    wait_count(page, r"of 41 jobs")
    assert before < 40


def test_level_filter_and_chip_sync_with_checkbox(app):
    page = app
    level_box(page, "Internship").check()
    wait_count(page, r"Showing 1 of 1 job$")
    assert titles(page) == ["Frontend Intern"]
    level_box(page, "Entry / fresher").check()
    wait_count(page, r"Showing 3 of 3 jobs$")
    assert set(titles(page)) == {"Frontend Intern", "Full Stack Developer (0-2 years)", "SDE I - Backend"}
    page.get_by_role("button", name="Remove filter: Internship").click()
    expect(level_box(page, "Internship")).not_to_be_checked()
    wait_count(page, r"Showing 2 of 2 jobs$")
    assert set(titles(page)) == {"Full Stack Developer (0-2 years)", "SDE I - Backend"}


def test_location_and_source_filters(app):
    page = app
    loc_box(page, "Remote").check()
    wait_count(page, r"of 2 jobs$")
    assert set(titles(page)) == {"SDE I - Backend", XSS_TITLE}
    loc_box(page, "Remote").uncheck()
    loc_box(page, "India").check()
    wait_count(page, r"of 37 jobs$")                       # everything except the two remote-only jobs
    loc_box(page, "India").uncheck()
    src = page.locator("#f-source")
    expect(src).to_contain_text("Greenhouse")
    expect(src).to_contain_text("Lever")
    src.get_by_label("Lever").check()
    wait_count(page, r"Showing 1 of 1 job$")
    assert titles(page) == ["SDE I - Backend"]
    expect(page.locator(JOBS).first.locator(".badge--src")).to_have_text("Lever")


def test_company_filter(app):
    page = app
    page.get_by_test_id("company").fill("gam")
    wait_count(page, r"Showing 1 of 1 job$")
    assert titles(page) == ["Frontend Intern"]


def test_posted_within_filter(app):
    page = app
    assert "SDE I - Backend" in titles(page)
    page.get_by_test_id("days").select_option("30")
    wait_count(page, r"of 38 jobs$")                       # SDE I was posted 60 days ago
    assert "SDE I - Backend" not in titles(page)
    expect(page.locator("[data-testid=chip]").filter(has_text="Last 30 days")).to_be_visible()


def test_sorting(app):
    page = app
    page.get_by_test_id("sort").select_option("company")
    expect(page).to_have_url(re.compile("sort=company"))
    page.wait_for_function("document.querySelectorAll('[data-testid=job]').length > 0")
    companies = page.locator(".job__meta span:first-child").all_inner_texts()
    assert companies == sorted(companies, key=str.lower) and len(companies) > 10
    page.get_by_test_id("sort").select_option("newest")
    expect(page.locator(TITLES).first).to_have_text("Full Stack Developer (0-2 years)")     # posted 0.5 days ago
    assert "sort=newest" in page.url and titles(page)[:2] == ["Full Stack Developer (0-2 years)", "Frontend Intern"]


def test_reset_restores_defaults_and_clears_url(app):
    page = app
    page.get_by_test_id("search").fill("intern")
    level_box(page, "Internship").check()
    page.get_by_role("radio", name="80+").click()
    page.get_by_test_id("clear-filters").click()
    wait_count(page, r"Showing 30 of 39 jobs")
    assert page.url.rstrip("/").endswith(str(page.url.split("/")[-1])) and "?" not in page.url
    expect(page.get_by_test_id("search")).to_have_value("")
    expect(level_box(page, "Internship")).not_to_be_checked()


def test_filters_are_restored_from_url(app):
    page = app
    page.goto(f"{page.base}/?q=intern&min=0&level=intern&sort=newest")
    page.wait_for_selector(JOBS)
    expect(page.get_by_test_id("search")).to_have_value("intern")
    expect(level_box(page, "Internship")).to_be_checked()
    expect(page.get_by_test_id("sort")).to_have_value("newest")
    assert titles(page) == ["Frontend Intern"]


def test_empty_state_offers_reset(app):
    page = app
    page.get_by_test_id("search").fill("zzzz-nothing")
    expect(page.get_by_role("heading", name="No jobs match these filters")).to_be_visible()
    wait_count(page, r"^0 jobs$")
    page.get_by_role("button", name="Reset filters").click()
    expect(page.locator(JOBS)).to_have_count(30)


# ------------------------------------------------------- tracking (save/apply/…)
def test_save_and_unsave(app):
    page = app
    target = row(page, "Frontend Intern")
    target.get_by_test_id("save").click()
    expect(page.get_by_test_id("toast")).to_contain_text("Saved")
    expect(target.get_by_test_id("save")).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_test_id("tab-saved")).to_contain_text("1")
    page.get_by_test_id("tab-saved").click()
    expect(page.locator(JOBS)).to_have_count(1)
    assert titles(page) == ["Frontend Intern"]
    page.get_by_test_id("save").click()                     # unsave: leaves the Saved tab
    expect(page.locator(JOBS)).to_have_count(0)
    page.get_by_test_id("tab-all").click()
    expect(row(page, "Frontend Intern")).to_be_visible()


def test_applied_jobs_leave_the_active_tab_and_live_in_the_applied_tab(app):
    page = app
    assert "SDE I - Backend" in titles(page)
    row(page, "SDE I - Backend").get_by_test_id("mark-applied").click()
    expect(page.get_by_test_id("toast")).to_contain_text("moved to the Applied tab")
    expect(row(page, "SDE I - Backend")).to_have_count(0)                              # gone from Active immediately
    expect(page.get_by_test_id("tab-applied").locator(".tab__count")).to_have_text("1")
    page.reload()
    page.wait_for_selector(JOBS)
    assert "SDE I - Backend" not in titles(page)                                       # ...and stays gone after a reload
    page.get_by_test_id("tab-applied").click()
    expect(page.locator(JOBS)).to_have_count(1)
    assert titles(page) == ["SDE I - Backend"]
    expect(row(page, "SDE I - Backend").locator(".badge--applied")).to_have_text("Applied")
    expect(page.get_by_test_id("stats")).to_contain_text("Applied")


def test_dismiss_hides_then_restore(app):
    page = app
    row(page, "Frontend Intern").get_by_test_id("dismiss").click()
    expect(page.get_by_test_id("toast")).to_contain_text("Dismissed")
    assert "Frontend Intern" not in titles(page)
    page.get_by_test_id("tab-dismissed").click()
    expect(page.locator(JOBS)).to_have_count(1)
    page.get_by_role("button", name="Restore Frontend Intern").click()
    expect(page.locator(JOBS)).to_have_count(0)
    page.get_by_test_id("tab-all").click()
    expect(row(page, "Frontend Intern")).to_be_visible()


def test_apply_link_opens_posting_in_new_tab_without_opener(app):
    page = app
    link = row(page, "Full Stack Developer (0-2 years)").get_by_test_id("apply")
    expect(link).to_have_attribute("target", "_blank")
    expect(link).to_have_attribute("rel", "noopener noreferrer")
    expect(link).to_have_attribute("href", re.compile(r"/api/health\?job=1$"))
    with page.context.expect_page() as popup:
        link.click()
    assert "job=1" in popup.value.url
    popup.value.close()


# --------------------------------------------------------------- detail drawer
def test_job_drawer_shows_detail_and_closes_with_escape_restoring_focus(app):
    page = app
    opener = row(page, "Full Stack Developer (0-2 years)").get_by_test_id("job-title")
    opener.click()
    drawer = page.get_by_test_id("job-drawer")
    expect(drawer).to_be_visible()
    expect(drawer).to_have_attribute("role", "dialog")
    expect(drawer.get_by_role("heading", name="Full Stack Developer (0-2 years)")).to_be_visible()
    expect(drawer).to_contain_text("Why it matches")
    expect(drawer).to_contain_text("Entry-level / fresher friendly")
    expect(page.get_by_test_id("job-desc")).to_contain_text("React, Node.js")
    expect(page.get_by_test_id("drawer-apply")).to_have_attribute("href", re.compile(r"job=1$"))
    expect(drawer.get_by_role("button", name="Close details")).to_be_focused()
    page.keyboard.press("Escape")
    expect(drawer).to_be_hidden()
    expect(opener).to_be_focused()


def test_drawer_traps_focus_and_scrim_closes(app):
    page = app
    page.get_by_test_id("job-title").first.click()
    drawer = page.get_by_test_id("job-drawer")
    expect(drawer.get_by_role("button", name="Close details")).to_be_focused()
    for _ in range(12):
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.closest('#job-drawer') !== null"), "focus escaped the dialog"
    page.mouse.click(50, 300)                                # the scrim
    expect(drawer).to_be_hidden()


def test_mark_applied_from_drawer_moves_the_row_out_of_active(app):
    page = app
    page.get_by_role("button", name="Frontend Intern", exact=True).click()
    page.get_by_test_id("job-drawer").get_by_role("button", name="Mark applied").click()
    expect(page.get_by_test_id("job-drawer").get_by_role("button", name="Unmark applied")).to_be_visible()   # drawer reflects the new state
    expect(row(page, "Frontend Intern")).to_have_count(0)                                                    # the list row left Active
    page.keyboard.press("Escape")
    page.get_by_test_id("tab-applied").click()
    expect(row(page, "Frontend Intern").locator(".badge--applied")).to_be_visible()


# ---------------------------------------------------------------------- profile
def test_profile_edit_rescoring_hides_excluded_titles(app):
    page = app
    open_profile(page)
    drawer = page.get_by_test_id("profile-drawer")
    expect(drawer).to_contain_text("resume.txt")
    skills = page.get_by_test_id("profile-skills")
    expect(skills.get_by_test_id("tag").filter(has_text="react")).to_be_visible()
    skills.get_by_role("button", name="Remove skill python").click()
    expect(skills.get_by_test_id("tag").filter(has_text="python")).to_have_count(0)
    page.get_by_label("Add a skill").or_(page.locator("#p-skill-input")).first.fill("Svelte")
    page.locator("#p-skill-form").get_by_role("button", name="Add").click()
    expect(skills.get_by_test_id("tag").filter(has_text="svelte")).to_be_visible()
    page.locator("#p-exclude-input").fill("Intern")
    page.locator("#p-exclude-form").get_by_role("button", name="Add").click()
    page.get_by_test_id("save-profile").click()
    expect(page.get_by_test_id("toast")).to_contain_text("re-matched")
    expect(drawer).to_be_hidden()
    expect(page.locator(JOBS).filter(has_text="Frontend Intern")).to_have_count(0)      # score forced below 50
    prof = page.request.get(page.base + "/api/profile").json()
    assert "python" not in prof["skills"] and "svelte" in prof["skills"] and prof["exclude_keywords"] == ["intern"]


def test_profile_location_preferences_persist(app):
    page = app
    open_profile(page)
    page.locator("#p-loc-remote").uncheck()
    page.get_by_test_id("save-profile").click()
    expect(page.get_by_test_id("toast")).to_contain_text("re-matched")
    assert page.request.get(page.base + "/api/profile").json()["locations"] == ["india"]
    open_profile(page)
    expect(page.locator("#p-loc-remote")).not_to_be_checked()
    expect(page.locator("#p-loc-india")).to_be_checked()


def test_resume_upload_replaces_profile(app, tmp_path):
    page = app
    cv = tmp_path / "newcv.txt"
    cv.write_text("Sam Rivera\nBackend Developer | Python • Django\nSkills: Python, Django, PostgreSQL, Docker, AWS, Redis\n"
                  "Built Django REST APIs with Python and PostgreSQL. Python Django Python.\nBE 2022 - 2026")
    open_profile(page)
    page.get_by_test_id("resume-input").set_input_files(str(cv))
    expect(page.get_by_test_id("toast")).to_contain_text("Resume processed")
    expect(page.get_by_role("heading", level=1)).to_have_text("Jobs for Sam")
    assert "django" in page.request.get(page.base + "/api/profile").json()["skills"]


def test_resume_upload_rejects_bad_file_with_message(app, tmp_path):
    page = app
    bad = tmp_path / "notes.txt"
    bad.write_text("nothing recognisable in here, only prose about hiking and gardening")
    open_profile(page)
    page.get_by_test_id("resume-input").set_input_files(str(bad))
    expect(page.locator("#profile-msg")).to_contain_text("Couldn’t read resume")
    expect(page.get_by_test_id("profile-drawer")).to_be_visible()
    assert all("422" in e for e in page.console_errors) and page.console_errors    # only the rejected upload
    page.console_errors.clear()


# ------------------------------------------------------------------------ crawl
def test_refresh_shows_progress_then_new_jobs(app):
    page = app
    btn = page.get_by_test_id("refresh")
    btn.click()
    expect(btn).to_be_disabled()
    expect(btn).to_contain_text("Crawling")
    expect(btn).to_have_attribute("aria-busy", "true")
    expect(page.get_by_test_id("toast")).to_contain_text("Refresh complete — 1 new job", timeout=15000)
    expect(btn).to_be_enabled()
    expect(btn).to_contain_text("Refresh jobs")
    page.get_by_test_id("search").fill("Freshly")
    expect(page.locator(JOBS)).to_have_count(1)
    assert titles(page) == ["Freshly Crawled Engineer"]


def test_crawl_in_progress_survives_page_reload(app):
    page = app
    page.get_by_test_id("refresh").click()
    page.reload()
    page.wait_for_selector(JOBS)
    expect(page.get_by_test_id("refresh")).to_contain_text("Crawling")
    expect(page.get_by_test_id("refresh")).to_contain_text("Refresh jobs", timeout=15000)


# ------------------------------------------------------- resilience & security
def test_api_failure_shows_error_state_and_retry_recovers(app):
    page = app
    page.route("**/api/jobs?*", lambda r: r.fulfill(status=500, json={"detail": "database is on fire"}))
    page.get_by_test_id("search").fill("x")
    expect(page.get_by_role("heading", name="Couldn’t load jobs")).to_be_visible()
    expect(page.locator("#state")).to_contain_text("database is on fire")
    page.unroute("**/api/jobs?*")
    page.get_by_role("button", name="Try again").click()
    expect(page.locator(JOBS).first).to_be_visible()
    assert all("500" in e for e in page.console_errors) and page.console_errors   # only the mocked 500
    page.console_errors.clear()


def test_scraped_content_cannot_inject_markup_or_script_urls(app):
    page = app
    page.get_by_test_id("search").fill("Evil")
    expect(page.locator(JOBS)).to_have_count(1)
    item = page.locator(JOBS).first
    expect(item.get_by_test_id("job-title")).to_have_text(XSS_TITLE)        # rendered as literal text
    expect(item).to_contain_text("<b>Evil</b> Inc")
    assert item.locator("img, b, script").count() == 0
    assert item.get_by_test_id("apply").count() == 0                         # javascript: URL rejected
    expect(item).to_contain_text("No link")
    item.get_by_test_id("job-title").click()
    expect(page.get_by_test_id("job-desc")).to_contain_text("<script>window.__xss=1</script>")
    assert page.evaluate("window.__xss") is None


# ---------------------------------------------------------- a11y & responsive
def test_basic_accessibility_contract(app):
    page = app
    assert page.locator("html").get_attribute("lang") == "en"
    for sel in ("header", "main", "aside", "h1"):
        assert page.locator(sel).count() >= 1, sel
    unnamed = page.evaluate("""() => [...document.querySelectorAll('button, a[href], input, select')]
        .filter(e => e.offsetParent !== null)
        .filter(e => !(e.getAttribute('aria-label') || e.textContent.trim() || (e.labels && e.labels.length) || e.getAttribute('title')))
        .map(e => e.outerHTML.slice(0, 80))""")
    assert unnamed == [], f"controls without an accessible name: {unnamed}"
    page.keyboard.press("Tab")
    expect(page.get_by_role("link", name="Skip to results")).to_be_focused()
    # dialogs are labelled
    for dlg in page.locator("[role=dialog]").all():
        assert dlg.get_attribute("aria-labelledby")
    # score has a text alternative
    assert "out of 100" in page.get_by_test_id("score").first.get_attribute("aria-label")


def test_mobile_layout_collapses_filters_and_does_not_overflow(app):
    page = app
    page.set_viewport_size({"width": 390, "height": 844})
    page.reload()
    page.wait_for_selector(JOBS)
    toggle = page.locator("#filters-toggle")
    expect(toggle).to_be_visible()
    expect(page.locator("#filters")).to_be_hidden()
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), "horizontal overflow"
    toggle.click()
    expect(page.locator("#filters")).to_be_visible()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    page.get_by_test_id("search").fill("intern")
    wait_count(page, r"Showing 1 of 1 job$")


def test_design_tokens_are_applied(app):
    """Guards the chosen DESIGN.md (Linear): near-black canvas and the lavender primary CTA."""
    page = app
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(1, 1, 2)"
    assert page.evaluate("getComputedStyle(document.querySelector('[data-testid=refresh]')).backgroundColor") == "rgb(94, 106, 210)"
    assert page.evaluate("getComputedStyle(document.querySelector('[data-testid=refresh]')).borderRadius") == "8px"


XSS_TITLE = '<img src=x onerror="window.__xss=1"> Developer'


# ------------------------------------------------------------------ salary / LPA
def salary_of(page, title):
    return row(page, title).get_by_test_id("salary")


def test_cards_show_lpa_or_say_not_listed(app):
    page = app
    expect(salary_of(page, "Full Stack Developer (0-2 years)")).to_have_text("₹12–18 LPA")
    expect(salary_of(page, "Frontend Intern")).to_have_text("₹3–3.6 LPA")                 # monthly stipend -> annual
    converted = salary_of(page, "SDE I - Backend")
    expect(converted).to_have_text("≈ ₹52.8–70.4 LPA")                                    # USD, converted
    expect(converted).to_have_attribute("title", re.compile("approximate exchange rates"))
    expect(page.locator(JOBS).filter(has_text="Software Engineer 00").get_by_test_id("salary")).to_have_text("Salary not listed")


def tick(page, label):
    page.locator("#f-salary").get_by_label(label, exact=True).check()


def test_salary_filter_lets_you_tick_several_ranges_at_once(app):
    page = app
    expect(page.get_by_test_id("salary-hint")).to_contain_text("3 of 41 jobs list a salary")
    expect(page.locator("#f-salary input")).to_have_count(7)                                       # 6 ranges + "Not listed"
    tick(page, "₹10–15 LPA")                                                                       # Full Stack Developer: ₹12–18
    wait_count(page, r"Showing 1 of 1 job$")
    assert titles(page) == ["Full Stack Developer (0-2 years)"]
    tick(page, "₹40 LPA and above")                                                                # + SDE I - Backend (≈ ₹52.8–70.4)
    wait_count(page, r"Showing 2 of 2 jobs$")
    assert set(titles(page)) == {"Full Stack Developer (0-2 years)", "SDE I - Backend"}            # OR, not AND and not "replace"
    tick(page, "Under ₹5 LPA")                                                                     # + Frontend Intern (₹3–3.6)
    wait_count(page, r"Showing 3 of 3 jobs$")
    from urllib.parse import parse_qs, urlparse
    assert parse_qs(urlparse(page.url).query)["lpa"] == ["10-15,40-,0-5"]                            # in the order you ticked them
    chips = page.locator("[data-testid=chip]")
    for label in ("Under ₹5 LPA", "₹10–15 LPA", "₹40 LPA and above"):
        expect(chips.filter(has_text=label)).to_have_count(1)
    chips.filter(has_text="₹10–15 LPA").get_by_role("button").click()                              # remove just one range
    expect(page.locator("#f-salary").get_by_label("₹10–15 LPA", exact=True)).not_to_be_checked()
    wait_count(page, r"Showing 2 of 2 jobs$")
    assert set(titles(page)) == {"SDE I - Backend", "Frontend Intern"}


def test_salary_ranges_overlap_and_not_listed_mixes_in_unlisted_jobs(app):
    page = app
    tick(page, "₹15–25 LPA")                                                                       # ₹12–18 overlaps 15–25 too
    wait_count(page, r"Showing 1 of 1 job$")
    assert titles(page) == ["Full Stack Developer (0-2 years)"]
    tick(page, "Not listed")
    wait_count(page, r"Showing 30 of 37 jobs$")                                                     # 36 unlisted (default Match>=50 hides 2 more) + the 1 range match
    assert "Full Stack Developer (0-2 years)" in titles(page)
    page.locator("#f-salary").get_by_label("₹15–25 LPA", exact=True).uncheck()
    wait_count(page, r"Showing 30 of 36 jobs$")                                                    # only the unlisted ones
    assert "Full Stack Developer (0-2 years)" not in titles(page)


def test_any_listed_shortcut_and_counts_next_to_each_range(app):
    page = app
    expect(page.get_by_test_id("salary-count-10-15")).to_have_text("1")
    expect(page.get_by_test_id("salary-count-0-5")).to_have_text("1")
    expect(page.get_by_test_id("salary-count-5-10")).to_have_text("0")
    expect(page.get_by_test_id("salary-count-none")).to_have_text("38")                               # 41 seeded - 3 that list a salary
    page.get_by_test_id("salary-all").click()
    for label in ("Under ₹5 LPA", "₹5–10 LPA", "₹10–15 LPA", "₹15–25 LPA", "₹25–40 LPA", "₹40 LPA and above"):
        expect(page.locator("#f-salary").get_by_label(label, exact=True)).to_be_checked()
    expect(page.locator("#f-salary").get_by_label("Not listed", exact=True)).not_to_be_checked()
    wait_count(page, r"Showing 3 of 3 jobs$")                                                      # exactly the jobs that list any salary
    counts_before = page.get_by_test_id("salary-count-10-15").inner_text()
    row(page, "Frontend Intern").get_by_test_id("save").click()                                     # stats refresh must not rebuild (lose focus)
    page.locator("#f-salary").get_by_label("₹5–10 LPA", exact=True).focus()
    expect(page.get_by_test_id("toast")).to_contain_text("Saved")
    expect(page.locator("#f-salary").get_by_label("₹5–10 LPA", exact=True)).to_be_focused()
    assert page.get_by_test_id("salary-count-10-15").inner_text() == counts_before


def test_any_listed_then_sort_highest_salary(app):
    page = app
    page.get_by_test_id("salary-all").click()
    wait_count(page, r"Showing 3 of 3 jobs$")
    page.get_by_test_id("sort").select_option("salary")
    expect(page.locator(TITLES).first).to_have_text("SDE I - Backend")
    assert titles(page) == ["SDE I - Backend", "Full Stack Developer (0-2 years)", "Frontend Intern"]
    assert "sort=salary" in page.url


def test_salary_filter_restored_from_url_and_reset(app):
    page = app
    page.goto(f"{page.base}/?lpa=10-15,40-&min=0")
    page.wait_for_selector(JOBS)
    for label, checked in (("₹10–15 LPA", True), ("₹40 LPA and above", True), ("Under ₹5 LPA", False), ("Not listed", False)):
        box = page.locator("#f-salary").get_by_label(label, exact=True)
        (expect(box).to_be_checked() if checked else expect(box).not_to_be_checked())
    assert set(titles(page)) == {"Full Stack Developer (0-2 years)", "SDE I - Backend"}
    page.get_by_test_id("clear-filters").click()
    expect(page.locator("#f-salary input:checked")).to_have_count(0)
    assert "lpa" not in page.url


def test_old_bookmarked_salary_links_still_work(app):
    page = app
    page.goto(f"{page.base}/?lpa=listed&min=0")                                                    # old "Salary listed"
    page.wait_for_selector(JOBS)
    assert set(titles(page)) == {"Full Stack Developer (0-2 years)", "SDE I - Backend", "Frontend Intern"}
    expect(page.locator("#f-salary input:checked")).to_have_count(6)
    page.goto(f"{page.base}/?lpa=25&min=0")                                                        # old "₹25 LPA+" -> ranges that reach 25+
    page.wait_for_selector(JOBS)
    assert titles(page) == ["SDE I - Backend"]
    expect(page.locator("#f-salary").get_by_label("₹25–40 LPA", exact=True)).to_be_checked()
    expect(page.locator("#f-salary").get_by_label("₹40 LPA and above", exact=True)).to_be_checked()
    expect(page.locator("#f-salary").get_by_label("₹15–25 LPA", exact=True)).not_to_be_checked()
    page.goto(f"{page.base}/?lpa=bogus,5-10,<script>&min=0")                                       # junk is ignored, valid parts kept
    page.wait_for_selector("#f-salary")
    expect(page.locator("#f-salary input:checked")).to_have_count(1)


def test_salary_checkboxes_are_keyboard_and_screen_reader_friendly(app):
    page = app
    group = page.get_by_role("group", name=re.compile("Salary"))
    expect(group).to_be_visible()
    first = page.locator("#f-salary").get_by_label("Under ₹5 LPA", exact=True)
    first.focus()
    page.keyboard.press("Space")
    expect(first).to_be_checked()
    expect(page.locator("[data-testid=chip]").filter(has_text="Under ₹5 LPA")).to_be_visible()


def test_drawer_shows_salary_detail(app):
    page = app
    row(page, "SDE I - Backend").get_by_test_id("job-title").click()
    drawer = page.get_by_test_id("job-drawer")
    expect(drawer).to_contain_text("≈ ₹52.8–70.4 LPA")
    expect(drawer).to_contain_text("listed as USD 60,000–80,000 annual")
    expect(drawer).to_contain_text("approximate rates")
    page.keyboard.press("Escape")
    row(page, "Software Engineer 00").get_by_test_id("job-title").click()
    expect(page.get_by_test_id("job-drawer")).to_contain_text("Not listed")


# ------------------------------------------------------------- Wellfound / YC (TinyFish)
def test_wellfound_source_is_filterable_and_preview_is_flagged(app):
    page = app
    expect(page.locator("#f-source")).to_contain_text("Wellfound")
    page.locator("#f-source").get_by_label("Wellfound").check()
    page.get_by_role("radio", name="Any").click()
    wait_count(page, r"Showing 1 of 1 job$")
    item = page.locator(JOBS).first
    expect(item.get_by_test_id("job-title")).to_have_text("Preview Only Tester")
    expect(item.locator(".badge--src")).to_have_text("Wellfound")
    preview = item.locator(".badge--preview")
    expect(preview).to_have_text("Preview")
    expect(preview).to_have_attribute("title", re.compile("confirm details"))
    expect(item.get_by_test_id("apply")).to_have_attribute("href", "https://wellfound.com/jobs/9001-preview-only-tester")
    expect(item.get_by_test_id("salary")).to_have_text("Salary not listed")


def test_regular_jobs_have_no_preview_badge(app):
    expect(app.locator(".badge--preview")).to_have_count(0)


# ------------------------------------------------------------- Cold email (vendored yc-outreach)
def load_batch(page):
    page.locator("#load").click()
    expect(page.locator("#loadStatus")).to_contain_text("20 of 26 companies loaded")


def test_navbar_links_between_jobs_and_cold_email(app):
    page = app
    nav = page.get_by_test_id("nav")
    expect(nav.get_by_role("link", name="Jobs")).to_have_attribute("aria-current", "page")
    nav.get_by_role("link", name="Cold email").click()
    expect(page).to_have_url(re.compile(r"/outreach$"))
    expect(page.get_by_role("heading", name="YC Founder Outreach")).to_be_visible()
    nav = page.get_by_test_id("nav")
    expect(nav.get_by_role("link", name="Cold email")).to_have_attribute("aria-current", "page")
    expect(nav.get_by_role("link", name="Jobs")).not_to_have_attribute("aria-current", "page")
    page.get_by_role("link", name=f"{APP_NAME} home").click()
    expect(page).to_have_url(re.compile(r"/$"))
    page.wait_for_selector(JOBS)


def test_cold_email_page_is_themed_and_batches_load(cold):
    page = cold
    options = page.locator("#batch option").all_inner_texts()
    assert options == ["Summer 2025 (2 companies)", "Winter 2024 (26 companies)"]      # newest first, "Unspecified" hidden
    assert not any("Unspecified" in o for o in options)
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(1, 1, 2)"
    assert page.evaluate("getComputedStyle(document.querySelector('#load')).backgroundColor") == "rgb(94, 106, 210)"
    assert page.title() == f"Cold email — {APP_NAME}"


def test_load_companies_paginates_twenty_at_a_time(cold):
    page = cold
    page.locator("#batch").select_option(label="Winter 2024 (26 companies)")
    load_batch(page)
    expect(page.locator("#list details")).to_have_count(20)
    expect(page.locator("#more")).to_have_text("Load 6 more (6 left)")
    page.locator("#more").click()
    expect(page.locator("#loadStatus")).to_contain_text("26 of 26 companies loaded")
    expect(page.locator("#list details")).to_have_count(26)
    expect(page.locator("#more")).to_be_hidden()


def test_draft_is_filled_from_your_template_and_mailto_is_built(cold):
    page = cold
    page.locator("#batch").select_option(label="Winter 2024 (26 companies)")
    page.locator('[data-k="my_name"]').fill("Hanish")
    page.locator('[data-k="github"]').fill("https://github.com/Hanish2022")
    page.get_by_role("button", name="Advanced: edit the full email text").click()
    page.locator('[data-k="subject"]').fill("Hello {company}")
    load_batch(page)
    item = page.locator("#list details").filter(has_text="Co01")
    expect(item.locator("summary .badge")).to_have_text("on website")         # co1 publishes addresses on its own site
    item.locator("summary").click()
    expect(item.locator(".subj")).to_have_text("Hello Co01")
    body = item.locator("pre")
    expect(body).to_contain_text("Hi Alice,")
    expect(body).to_contain_text("I came across Co01 — builds thing 1.")
    expect(body).to_contain_text("Thanks,\nHanish")
    expect(body).to_contain_text("GitHub: https://github.com/Hanish2022")
    expect(body).not_to_contain_text("Resume:")                               # empty fields drop their whole line
    expect(body).not_to_contain_text("Portfolio:")
    href = item.locator("a.mail").get_attribute("href")
    assert href.startswith("mailto:alice%40co1.example?subject=Hello%20Co01&body=")
    other = page.locator("#list details").filter(has_text="Co02")
    expect(other.locator("summary .badge")).to_have_text("guessed")           # no published address -> pattern guess
    page.reload()                                                             # details + template survive (localStorage)
    expect(page.locator('[data-k="my_name"]')).to_have_value("Hanish")


def test_copy_buttons_and_sent_tracking_survive_reload(cold):
    page = cold
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.locator("#batch").select_option(label="Winter 2024 (26 companies)")
    load_batch(page)
    item = page.locator("#list details").filter(has_text="Co02")
    item.locator("summary").click()
    item.get_by_role("button", name="Copy address").click()
    expect(item.get_by_role("button", name="Copied")).to_be_visible()
    assert page.evaluate("navigator.clipboard.readText()") == "alice@co2.example"
    item.get_by_label("Mark as sent").check()
    expect(page.locator("#shown")).to_contain_text("1 sent")
    page.reload()
    expect(page.locator("#load")).to_be_enabled()
    page.locator("#batch").select_option(label="Winter 2024 (26 companies)")
    page.locator("#load").click()                                             # cached in the browser: instant
    expect(page.locator("#list details")).to_have_count(20)
    expect(page.locator("#list details").filter(has_text="Co02")).to_have_class(re.compile("sent"))
    page.locator("#hideSent").check()
    expect(page.locator("#list details:visible")).to_have_count(19)
    page.locator("#q").fill("co03")
    expect(page.locator("#list details:visible")).to_have_count(1)


def test_cold_email_search_and_email_type_filters(cold):
    page = cold
    page.locator("#batch").select_option(label="Winter 2024 (26 companies)")
    load_batch(page)
    page.locator("#st").select_option("site")
    expect(page.locator("#list details:visible")).to_have_count(1)
    page.locator("#st").select_option("guess")
    expect(page.locator("#list details:visible")).to_have_count(19)
    page.locator("#st").select_option("")
    page.locator("#q").fill("alice founder5")
    expect(page.locator("#list details:visible")).to_have_count(1)


def test_cold_email_renders_hostile_yc_data_as_text_and_csp_blocks_exfiltration(cold):
    page = cold
    page.locator("#batch").select_option(label="Winter 2024 (26 companies)")
    load_batch(page)
    evil = page.locator("#list details").first
    expect(evil.locator(".name")).to_have_text("Evil<img src=x onerror=window.__xss=1>")      # upstream escapes; we didn't weaken it
    assert evil.locator("img").count() == 0 and page.evaluate("window.__xss") is None
    evil.locator("summary").click()
    assert evil.locator('a[href^="javascript:"]').count() == 0                                   # only http(s) links are rendered
    # CSP: the page may call Apify (optional feature) and itself, nothing else
    blocked = page.evaluate("fetch('https://example.com/steal', {mode: 'no-cors'}).then(() => 'sent', () => 'blocked')")
    assert blocked == "blocked"
    assert page.console_errors and all("Content Security Policy" in e for e in page.console_errors)
    page.console_errors.clear()


def test_cold_email_page_api_errors_are_shown_not_swallowed(cold):
    page = cold
    page.route("**/api/yc?action=companies*", lambda r: r.fulfill(status=502, json={"error": "YC unreachable"}))
    page.locator("#batch").select_option(label="Winter 2024 (26 companies)")
    page.locator("#load").click()
    expect(page.locator("#loadStatus")).to_contain_text("Couldn't load this batch: YC unreachable")
    assert page.console_errors and all("502" in e for e in page.console_errors)
    page.console_errors.clear()


def test_cold_email_mobile_layout_and_keyboard_access(cold):
    page = cold
    page.set_viewport_size({"width": 390, "height": 844})
    page.reload()
    expect(page.locator("#load")).to_be_enabled()
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), "horizontal overflow"
    expect(page.get_by_test_id("nav").get_by_role("link", name="Jobs")).to_be_visible()
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.closest('header') !== null")                 # first tab stop is the navbar


# --------------------------------------------- Cold email: the plain-language layer (app/static/outreach.js)
def test_step1_is_marked_optional_and_explains_why(cold):
    page = cold
    expect(page.get_by_role("heading", name=re.compile(r"1\. Your name and links"))).to_contain_text("(all optional)")
    expect(page.locator(".oc-note").first).to_contain_text("Leave any field empty and that line is simply left out")
    expect(page.locator(".oc-note").first).to_contain_text("nothing is uploaded or attached")
    for key, text in {"portfolio": "Portfolio or website (optional)", "github": "GitHub profile (optional)", "resume": "Link to your resume (optional)"}.items():
        field = page.locator(f'[data-k="{key}"]')
        expect(page.get_by_label(text)).to_have_count(1)
        assert field.get_attribute("required") is None                        # nothing is actually required
    assert page.locator('[data-k="my_name"]').get_attribute("required") is None


def test_empty_optional_fields_simply_vanish_from_the_email(cold):
    page = cold
    for key in ("portfolio", "github", "resume"):
        page.locator(f'[data-k="{key}"]').fill("")
    page.locator("#batch").select_option(label="Winter 2024 (26 companies)")
    load_batch(page)
    item = page.locator("#list details").filter(has_text="Co03")
    item.locator("summary").click()
    body = item.locator("pre")
    for label in ("Portfolio:", "GitHub:", "Resume:"):
        expect(body).not_to_contain_text(label)
    expect(body).to_contain_text("Thanks,\nJane Doe")


def test_your_name_is_prefilled_from_your_profile_but_never_overwrites_what_you_typed(cold):
    page = cold
    expect(page.locator('[data-k="my_name"]')).to_have_value("Jane Doe")      # the Jobs-page profile name
    page.locator('[data-k="my_name"]').fill("Hanish")
    page.reload()
    expect(page.locator("#load")).to_be_enabled()
    expect(page.locator('[data-k="my_name"]')).to_have_value("Hanish")


def test_preview_switches_to_a_real_company_once_loaded(cold):
    page = cold
    page.locator("#batch").select_option(label="Winter 2024 (26 companies)")
    load_batch(page)
    expect(page.locator("#oc-preview-for")).to_have_text("(for Evil<img src=x onerror=window.__xss=1>)")   # first loaded company, as text
    assert page.locator("#oc-preview-for img").count() == 0 and page.evaluate("window.__xss") is None


def test_verified_email_tools_are_tucked_away_until_asked_for(cold):
    page = cold
    toggle = page.get_by_role("button", name=re.compile("Advanced: find verified emails"))
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(page.locator("#token")).to_be_hidden()
    toggle.click()
    expect(page.locator("#token")).to_be_visible()
    expect(page.get_by_role("button", name="Hide verified-email options")).to_have_attribute("aria-expanded", "true")
    page.get_by_role("button", name="Hide verified-email options").click()
    expect(page.locator("#token")).to_be_hidden()


def test_company_view_explains_the_address_and_uses_clear_button_names(cold):
    page = cold
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.locator("#batch").select_option(label="Winter 2024 (26 companies)")
    load_batch(page)
    site = page.locator("#list details").filter(has_text="Co01")
    site.locator("summary").click()
    expect(site.locator(".r b").first).to_have_text("Send to")
    expect(site.locator(".oc-hint")).to_contain_text("published on the company's own website")
    guess = page.locator("#list details").filter(has_text="Co02")
    guess.locator("summary").click()
    expect(guess.locator(".oc-hint")).to_contain_text("a guess")
    expect(guess.locator(".oc-hint")).to_contain_text("may bounce")
    expect(guess.locator(".oc-draft-head")).to_have_text("Your email")
    for name in ("Copy message", "Copy address", "Copy subject", "Open in email app"):
        expect(guess.get_by_text(name, exact=True)).to_be_visible()
    guess.get_by_role("button", name="Copy message").click()
    expect(guess.get_by_role("button", name="Copied")).to_be_visible()
    assert "Hi Alice," in page.evaluate("navigator.clipboard.readText()")
    expect(page.locator(".oc-legend")).to_contain_text("guessed = built from the founder's name")
    expect(page.locator("#st option").first).to_have_text("Any email type")


def test_expand_all_still_opens_and_closes_everything_and_new_items_get_the_same_treatment(cold):
    page = cold
    page.locator("#batch").select_option(label="Winter 2024 (26 companies)")
    load_batch(page)
    expand = page.locator("#all")
    expect(expand).to_have_text("Expand all")                                   # upstream keys off this exact text
    expand.click()
    expect(page.locator("#list details[open]")).to_have_count(20)
    expect(expand).to_have_text("Collapse all")
    expand.click()
    expect(page.locator("#list details[open]")).to_have_count(0)
    page.locator("#more").click()
    expect(page.locator("#list details")).to_have_count(26)
    page.locator("#list details").last.locator("summary").click()
    expect(page.locator("#list details").last.get_by_label("Mark as sent")).to_be_visible()   # decorated after "Load more" too


# ------------------------------------------- Cold email step 2, simplified: two plain boxes, no template editing
def open_advanced(page):
    page.get_by_role("button", name="Advanced: edit the full email text").click()
    expect(page.locator('[data-k="body"]')).to_be_visible()


def test_step2_is_two_plain_boxes_and_shows_no_curly_braces(cold):
    page = cold
    expect(page.get_by_role("heading", name="2. Your message")).to_be_visible()
    expect(page.get_by_label("A little about you (1–2 sentences)")).to_be_visible()
    expect(page.get_by_label("What are you looking for?")).to_be_visible()
    expect(page.locator("#oc-goal option")).to_have_text(["An internship", "A full-time job", "Just a short chat"])
    panel = page.locator("section.panel").first
    visible_text = panel.evaluate("""n => { const w = document.createTreeWalker(n, NodeFilter.SHOW_TEXT); const out = [];
        while (w.nextNode()) { const t = w.currentNode; const e = t.parentElement;
          if (e && e.offsetParent !== null) out.push(t.nodeValue); } return out.join(' ') }""")
    assert "{" not in visible_text and "}" not in visible_text, f"template syntax leaked into the normal view: {visible_text[:200]}"
    assert panel.locator("textarea:visible").count() == 1                        # only "about you" is a free-text box
    expect(page.locator("#vars")).to_be_hidden()
    expect(page.locator('[data-k="body"]')).to_be_hidden()                       # the raw template is collapsed away


def test_the_email_is_built_from_the_two_boxes_and_previews_live(cold):
    page = cold
    expect(page.locator("#oc-preview-for")).to_have_text("(example company)")
    expect(page.locator("#oc-about-nudge")).to_be_visible()                      # nothing written yet -> gentle tip
    page.get_by_label("A little about you (1–2 sentences)").fill("I'm a final-year CS student who builds MERN apps.")
    preview = page.locator("#oc-preview-body")
    expect(preview).to_have_text(re.compile(
        r"^Hi Jane,\n\nI came across Acme — builds rockets for small teams\.\n\nI'm a final-year CS student who builds MERN apps\.\n\n"
        r"I'm looking for an internship and would love to know if Acme has any openings\.\n\nThanks,\nJane Doe$"))
    expect(page.locator("#oc-preview-subject")).to_have_text("Subject: Internship at Acme?")
    expect(page.locator("#oc-about-nudge")).to_be_hidden()
    page.get_by_label("What are you looking for?").select_option("job")
    expect(page.locator("#oc-preview-subject")).to_have_text("Subject: Interested in a role at Acme")
    expect(preview).to_contain_text("I'm looking for a full-time role and would love to know if Acme is hiring.")
    page.get_by_label("What are you looking for?").select_option("chat")
    expect(page.locator("#oc-preview-subject")).to_have_text("Subject: Quick chat about Acme?")
    expect(preview).to_contain_text("I'd love a short chat to learn more about what you're building at Acme.")


def test_leaving_the_about_box_empty_just_drops_that_paragraph(cold):
    page = cold
    text = page.locator("#oc-preview-body").inner_text()
    assert "\n\n\n" not in text and text.count("\n\n") == 3                      # greeting | intro | ask | signature, no gaps


def test_what_you_write_reaches_every_company_email_and_survives_a_reload(cold):
    page = cold
    page.get_by_label("A little about you (1–2 sentences)").fill("I built a delivery app (React, Node, MongoDB).")
    page.get_by_label("What are you looking for?").select_option("job")
    page.locator("#batch").select_option(label="Winter 2024 (26 companies)")
    load_batch(page)
    item = page.locator("#list details").filter(has_text="Co04")
    item.locator("summary").click()
    expect(item.locator(".subj")).to_have_text("Interested in a role at Co04")
    expect(item.locator("pre")).to_contain_text("I built a delivery app (React, Node, MongoDB).")
    expect(item.locator("pre")).to_contain_text("I'm looking for a full-time role and would love to know if Co04 is hiring.")
    assert "[" not in item.locator("pre").inner_text()                           # nothing left for the user to fill in
    assert page.locator(".oc-draft-warn:visible").count() == 0
    page.reload()
    expect(page.locator("#load")).to_be_enabled()
    expect(page.get_by_label("A little about you (1–2 sentences)")).to_have_value("I built a delivery app (React, Node, MongoDB).")
    expect(page.get_by_label("What are you looking for?")).to_have_value("job")
    expect(page.locator("#oc-preview-subject")).to_have_text("Subject: Interested in a role at Acme")


def test_text_you_type_is_shown_as_text_and_braces_you_type_still_work(cold):
    page = cold
    page.get_by_label("A little about you (1–2 sentences)").fill("<img src=x onerror=window.__xss=1> I like {company}")
    expect(page.locator("#oc-preview-body")).to_contain_text("<img src=x onerror=window.__xss=1> I like Acme")
    assert page.locator("#oc-preview-body img").count() == 0 and page.evaluate("window.__xss") is None


def test_advanced_shows_the_full_text_and_rebuilds_when_the_boxes_change(cold):
    page = cold
    expect(page.locator("#oc-advanced")).to_have_attribute("aria-expanded", "false")
    open_advanced(page)
    expect(page.locator("#oc-advanced")).to_have_attribute("aria-expanded", "true")
    expect(page.locator("#oc-advanced-panel")).to_contain_text("Changing the two boxes above rebuilds this text")
    body = page.locator('[data-k="body"]')
    expect(body).to_have_value(re.compile(r"^Hi \{first_name\},\n\nI came across \{company\} — \{one_liner\}\."))
    body.fill("Hello {first_name}, my own words about {company}.")
    expect(page.locator("#oc-preview-body")).to_have_text("Hello Jane, my own words about Acme.")
    expect(page.locator("#oc-custom-note")).to_be_visible()                      # you took over the text: we say so
    page.get_by_label("A little about you (1–2 sentences)").fill("Back to the simple boxes.")
    expect(page.locator("#oc-preview-body")).to_contain_text("Back to the simple boxes.")
    expect(page.locator("#oc-custom-note")).to_be_hidden()
    expect(body).to_have_value(re.compile(r"Back to the simple boxes\."))


def test_bracket_warnings_only_appear_for_hand_written_text_and_disappear_when_fixed(cold):
    page = cold
    expect(page.locator("#oc-bracket-warning")).to_be_hidden()                   # the generated email never contains brackets
    page.locator("#batch").select_option(label="Winter 2024 (26 companies)")
    load_batch(page)
    item = page.locator("#list details").filter(has_text="Co02")
    item.locator("summary").click()
    expect(item.locator(".oc-draft-warn")).to_be_hidden()
    open_advanced(page)
    page.locator('[data-k="body"]').fill("Hi {first_name}, [say something about you] thanks")
    expect(page.locator("#oc-bracket-warning")).to_be_visible()
    expect(item.locator(".oc-draft-warn")).to_be_visible()
    page.locator('[data-k="body"]').fill("Hi {first_name}, I build things. Thanks")
    expect(page.locator("#oc-bracket-warning")).to_be_hidden()
    expect(item.locator(".oc-draft-warn")).to_be_hidden()


def test_a_message_you_wrote_yourself_earlier_is_kept_and_flagged(page, live):
    page.add_init_script("""localStorage.setItem('yc-outreach-v2', JSON.stringify({my_name: 'Me', subject: 'My subject', body: 'My own body {company}'}));""")
    page.goto(live + "/outreach")
    expect(page.locator("#load")).to_be_enabled()
    expect(page.locator("#oc-custom-note")).to_be_visible()
    expect(page.locator("#oc-advanced")).to_have_attribute("aria-expanded", "true")     # opened so you can see what's being used
    expect(page.locator('[data-k="subject"]')).to_have_value("My subject")
    expect(page.locator("#oc-preview-body")).to_have_text("My own body Acme")    # not overwritten on load


def test_upstreams_default_or_the_previous_starter_is_upgraded_to_the_simple_message(page, live):
    old_starter = ("Hi {first_name},\\n\\nI came across {company} — {one_liner}. It looks like a great team to learn from and contribute to.\\n\\n"
                   "[Write 1–2 sentences about you]\\n\\nIf there's a role...")
    page.add_init_script(f"""localStorage.setItem('yc-outreach-v2', JSON.stringify({{my_name: 'Me', subject: 'Interested in working at {{company}}', body: "{old_starter}"}}));""")
    page.goto(live + "/outreach")
    expect(page.locator("#load")).to_be_enabled()
    expect(page.locator("#oc-custom-note")).to_be_hidden()
    expect(page.locator("#oc-preview-body")).to_contain_text("I'm looking for an internship and would love to know if Acme has any openings.")
    assert "[" not in page.locator("#oc-preview-body").inner_text()


def test_only_company_rows_are_details_elements_so_upstreams_own_code_never_breaks(cold):
    """Upstream loops over EVERY <details> on the page and expects company data on each (regression: an 'Advanced' <details> crashed it)."""
    page = cold
    assert page.evaluate("document.querySelectorAll('details:not(#list details)').length") == 0
    page.locator("#batch").select_option(label="Winter 2024 (26 companies)")
    load_batch(page)
    page.locator("#q").fill("co0")                                               # exercises upstream's filter() loop
    page.locator("#st").select_option("guess")
    page.get_by_label("A little about you (1–2 sentences)").fill("Typing refreshes open drafts via upstream's update() loop.")
    expect(page.locator("#list details:visible").first).to_be_visible()


def test_cold_email_step2_on_a_phone(cold):
    page = cold
    page.set_viewport_size({"width": 390, "height": 844})
    page.reload()
    expect(page.locator("#load")).to_be_enabled()
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), "horizontal overflow"
    about = page.get_by_label("A little about you (1–2 sentences)")
    goal = page.get_by_label("What are you looking for?")
    a, g = about.bounding_box(), goal.bounding_box()
    assert g["y"] > a["y"] + a["height"] - 1                                     # stacked, not side by side


# ------------------------------------------------------------------- redesign: navbar, colour coding, components
def rgb_of(page, selector, prop="color"):
    return page.evaluate("([s, p]) => getComputedStyle(document.querySelector(s))[p]", [selector, prop])


def test_navbar_is_one_row_brand_and_links_grouped_left_actions_right(app):
    page = app
    bar = page.locator("header.topnav .topnav__inner").bounding_box()
    assert bar["height"] == 60 and bar["y"] == 0
    brand, nav, right = (page.locator(s).bounding_box() for s in (".brand", "[data-testid=nav]", ".topnav__right"))
    assert brand["x"] < nav["x"] and nav["x"] - (brand["x"] + brand["width"]) < 40, "links sit right next to the brand (not floating in the middle)"
    assert right["x"] + right["width"] > bar["x"] + bar["width"] - 30, "actions are flush right"
    assert abs((brand["y"] + brand["height"] / 2) - (nav["y"] + nav["height"] / 2)) < 2 and abs((nav["y"] + nav["height"] / 2) - (right["y"] + right["height"] / 2)) < 2
    current = page.locator(".navlink[aria-current=page]")
    expect(current).to_have_text("Jobs")
    marker = page.evaluate("getComputedStyle(document.querySelector('.navlink[aria-current=page]'), '::before')")
    assert marker["width"] == "26px" and marker["height"] == "3px" and "rgb" in marker["boxShadow"]
    expect(page.locator(".navlink:not([aria-current])")).to_have_count(1)
    expect(page.get_by_test_id("refresh")).to_be_visible()
    expect(page.get_by_test_id("refresh")).to_contain_text("Refresh jobs")
    expect(page.locator("#avatar")).to_have_text("JD")                                # initials from the profile name (Jane Doe)


def test_navbar_is_sticky_and_keyboard_reachable(app):
    page = app
    page.evaluate("window.scrollTo(0, 2000)")
    assert page.locator("header.topnav").bounding_box()["y"] == 0
    page.evaluate("window.scrollTo(0, 0)")
    page.keyboard.press("Tab")                                                        # skip link
    page.keyboard.press("Tab")                                                        # brand
    expect(page.get_by_role("link", name=f"{APP_NAME} home")).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.get_by_test_id("nav").get_by_role("link", name="Jobs")).to_be_focused()
    outline = page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
    assert outline == "solid"                                                         # visible focus ring


def test_freshness_dot_reflects_how_recent_the_last_refresh_is(app):
    page = app
    meta = page.get_by_test_id("run-meta")
    expect(meta).to_have_attribute("data-state", "fresh")                              # seeded run just finished
    dot = page.evaluate("getComputedStyle(document.querySelector('#run-meta'), '::before').backgroundColor")
    assert dot == "rgb(89, 212, 153)"
    page.get_by_test_id("refresh").click()
    expect(meta).to_have_attribute("data-state", "busy")
    expect(meta).to_have_attribute("data-state", "fresh", timeout=15000)


def test_levels_have_fixed_meaningful_colours(app):
    page = app
    page.get_by_role("radio", name="Any").click()
    wait_count(page, r"Showing 30 of 41 jobs")
    want = {"Internship": "rgb(154, 163, 255)", "Entry level": "rgb(89, 212, 153)", "Senior": "rgb(255, 97, 97)", "Level n/a": "rgb(163, 168, 177)"}
    seen = {}
    page.get_by_test_id("search").fill("")
    for label in list(want):
        page.get_by_test_id("search").fill({"Internship": "Frontend Intern", "Entry level": "Full Stack Developer (0", "Senior": "Senior Staff", "Level n/a": "Preview Only"}[label])
        tag = page.locator(f".badge--level:has-text('{label}')").first
        expect(tag).to_be_visible()
        seen[label] = tag.evaluate("e => getComputedStyle(e).color")
    assert seen == want, seen


def test_score_rings_use_the_four_score_bands(app):
    page = app
    page.get_by_role("radio", name="Any").click()
    page.get_by_test_id("search").fill("Senior Staff")
    ring = page.get_by_test_id("score").first
    expect(ring).to_have_class(re.compile("score--gray"))                              # 25 -> below 40
    assert ring.evaluate("e => getComputedStyle(e).getPropertyValue('--p').trim()") == "25"
    page.get_by_test_id("search").fill("Full Stack Developer (0")
    top = page.get_by_test_id("score").first
    expect(top).to_have_class(re.compile("score--green"))
    assert "conic-gradient" in top.evaluate("e => getComputedStyle(e).backgroundImage")
    assert top.evaluate("e => getComputedStyle(e).borderRadius") in ("50%", "24px")
    expect(top).to_have_attribute("aria-label", re.compile(r"Match score \d+ out of 100"))



def test_remote_sources_and_salary_are_coded(app):
    page = app
    item = row(page, "SDE I - Backend")
    expect(item.locator(".badge--remote")).to_have_text("Remote")
    assert item.locator(".badge--remote").evaluate("e => getComputedStyle(e).color") == "rgb(87, 193, 255)"
    assert item.get_by_test_id("salary").evaluate("e => getComputedStyle(e).color") == "rgb(89, 212, 153)"
    none = page.locator(JOBS).filter(has_text="Software Engineer 00").get_by_test_id("salary")
    assert none.evaluate("e => getComputedStyle(e).color") == "rgb(98, 102, 109)"
    dot = item.locator(".badge--src .dot")
    assert dot.get_attribute("data-src") == "lever" and dot.evaluate("e => getComputedStyle(e).backgroundColor") == "rgb(87, 193, 255)"
    page.locator("#f-source").get_by_label("Lever").check()
    expect(page.locator("#f-source .dot[data-src=lever]")).to_be_visible()


def test_saved_and_applied_rows_get_colour_accents_and_tags(app):
    page = app
    target = row(page, "Frontend Intern")
    target.get_by_test_id("save").click()
    expect(target.locator(".badge--saved")).to_have_text("Saved")
    assert target.evaluate("e => getComputedStyle(e).boxShadow").startswith("rgb(87, 193, 255)")           # blue accent bar
    assert target.get_by_test_id("save").evaluate("e => getComputedStyle(e).color") == "rgb(87, 193, 255)"
    target.get_by_test_id("mark-applied").click()                                                            # applying moves it to the Applied tab
    page.get_by_test_id("tab-applied").click()
    applied = row(page, "Frontend Intern")
    expect(applied.locator(".badge--applied")).to_have_text("Applied")
    assert applied.evaluate("e => getComputedStyle(e).boxShadow").startswith("rgb(89, 212, 153)")           # green accent
    assert applied.get_by_test_id("mark-applied").evaluate("e => getComputedStyle(e).color") == "rgb(89, 212, 153)"


def test_kpi_cards_have_colour_coded_icon_tiles(app):
    page = app
    tiles = page.locator(".stat__icon")
    expect(tiles).to_have_count(4)
    colours = tiles.evaluate_all("els => els.map(e => getComputedStyle(e).color)")
    assert colours == ["rgb(154, 163, 255)", "rgb(89, 212, 153)", "rgb(87, 193, 255)", "rgb(255, 154, 87)"]
    assert all(tiles.nth(i).get_attribute("aria-hidden") == "true" for i in range(4))
    expect(page.get_by_test_id("stats").locator("dd").first).to_have_text("41")


def test_status_tabs_have_a_sliding_indicator_and_coloured_counts(app):
    page = app
    thumb = page.locator(".tabs__thumb")
    first = page.get_by_test_id("tab-all").bounding_box()
    t0 = thumb.bounding_box()
    assert abs(t0["x"] - first["x"]) < 2 and abs(t0["width"] - first["width"]) < 2
    row(page, "Frontend Intern").get_by_test_id("save").click()
    expect(page.get_by_test_id("tab-saved").locator(".tab__count")).to_have_text("1")
    assert page.get_by_test_id("tab-saved").locator(".tab__count").evaluate("e => getComputedStyle(e).color") == "rgb(87, 193, 255)"
    page.get_by_test_id("tab-saved").click()
    page.wait_for_timeout(450)                                                          # transition is 260ms
    sel, t1 = page.get_by_test_id("tab-saved").bounding_box(), thumb.bounding_box()
    assert abs(t1["x"] - sel["x"]) < 2 and abs(t1["width"] - sel["width"]) < 2 and t1["x"] > t0["x"]
    page.get_by_test_id("tab-applied").click()
    expect(page.get_by_test_id("tab-applied")).to_have_attribute("aria-selected", "true")


def test_filter_checkboxes_carry_the_same_colours_as_the_tags(app):
    page = app
    dots = page.locator("#f-level .dot").evaluate_all("els => els.map(e => getComputedStyle(e).backgroundColor)")
    assert dots == ["rgb(154, 163, 255)", "rgb(89, 212, 153)", "rgb(255, 197, 51)", "rgb(255, 97, 97)", "rgb(163, 168, 177)"]


def test_phone_keeps_the_same_single_row_with_icon_only_controls(app):
    page = app
    page.set_viewport_size({"width": 390, "height": 844})
    page.reload()
    page.wait_for_selector(JOBS)
    bar = page.locator("header.topnav .topnav__inner").bounding_box()
    nav = page.get_by_test_id("nav").bounding_box()
    assert bar["height"] == 56 and nav["y"] < bar["y"] + bar["height"], "the links stay in the top bar (no detached bottom dock)"
    assert page.evaluate("getComputedStyle(document.querySelector('[data-testid=nav]')).position") == "relative"
    for name in ("Jobs", "Cold email"):
        link = page.get_by_test_id("nav").get_by_role("link", name=name)
        expect(link).to_be_visible()                                                   # icon only, still named for screen readers
        assert link.bounding_box()["width"] < 60
    refresh = page.get_by_test_id("refresh")
    assert refresh.bounding_box()["width"] < 60
    expect(refresh).to_have_text("Refresh jobs")
    expect(refresh).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def test_cold_email_page_shares_the_shell_and_colour_tags(cold):
    page = cold
    expect(page.get_by_test_id("nav").locator(".navlink[aria-current=page]")).to_have_text("Cold email")
    expect(page.get_by_test_id("safety-note")).to_have_text("Nothing is sent automatically")
    assert page.locator("header.topnav .topnav__inner").bounding_box()["height"] == 60
    assert page.evaluate("getComputedStyle(document.querySelector('.bar')).top") == "60px"            # sticky toolbar sits under the nav
    page.locator("#batch").select_option(label="Winter 2024 (26 companies)")
    page.locator("#load").click()
    expect(page.locator("#loadStatus")).to_contain_text("20 of 26 companies loaded")
    site = page.locator("#list details").filter(has_text="Co01").locator("summary .badge")
    guess = page.locator("#list details").filter(has_text="Co02").locator("summary .badge")
    assert site.evaluate("e => getComputedStyle(e).color") == "rgb(87, 193, 255)"                    # found on website = blue
    assert guess.evaluate("e => getComputedStyle(e).color") == "rgb(255, 197, 51)"                   # guessed = yellow
    assert page.evaluate("getComputedStyle(document.querySelector('a[href*=\"apify.com\"]')).color") == "rgb(154, 163, 255)"       # links use the readable violet, not the brand one



def test_every_score_band_renders_with_its_own_ring_colour(app):
    """Drives the real page's renderer with one job per band (no formula re-implemented in the test)."""
    page = app
    colours = page.evaluate("""() => {
        const out = {};
        for (const n of [100, 80, 79, 60, 59, 40, 39, 0]) {
          const li = renderJob({ id: 9000 + n, title: 'T' + n, company: 'C', url: 'https://example.com', location: 'x', remote: false, score: n,
                                 level: 'entry', status: 'new', source: 'lever', matched: [], tags: [], salary_lpa: '', first_seen: '2026-10-01T00:00:00Z' });
          document.body.append(li);
          out[n] = [li.querySelector('.score').className.match(/score--(\\w+)/)[1], getComputedStyle(li.querySelector('.score')).getPropertyValue('--p').trim()];
          li.remove();
        }
        return out;
    }""")
    assert colours == {"100": ["green", "100"], "80": ["green", "80"], "79": ["blue", "79"], "60": ["blue", "60"],
                       "59": ["yellow", "59"], "40": ["yellow", "40"], "39": ["gray", "39"], "0": ["gray", "0"]}


# ---------------------------------------------- navbar robustness: never scattered, overlapping, wrapping or un-fixed
BAR_PROBE = """() => {
  const q = s => document.querySelector(s), bx = e => { const b = e.getBoundingClientRect(); return {x: b.x, y: b.y, r: b.x + b.width, b: b.y + b.height, h: b.height} };
  const bar = bx(q('.topnav__inner')), parts = ['.brand', '.topnav__nav', ...[...document.querySelectorAll('.topnav__right > *')].filter(e => e.offsetParent !== null).map(e => '.topnav__right > :nth-child(' + ([...e.parentNode.children].indexOf(e) + 1) + ')')].map(s => [s, bx(q(s))]);
  const problems = [];
  for (const [s, b] of parts) {
    if (b.x < -0.5 || b.r > innerWidth + 0.5) problems.push(s + ' leaves the screen');
    if (b.y < bar.y - 0.5 || b.b > bar.b + 0.5) problems.push(s + ' sticks out of the bar');
  }
  for (let i = 0; i < parts.length; i++) for (let j = i + 1; j < parts.length; j++) {
    const a = parts[i][1], c = parts[j][1];
    if (!(a.r <= c.x + 0.5 || c.r <= a.x + 0.5 || a.b <= c.y + 0.5 || c.b <= a.y + 0.5)) problems.push(parts[i][0] + ' overlaps ' + parts[j][0]);
  }
  const mids = parts.map(([s, b]) => b.y + b.h / 2);
  if (Math.max(...mids) - Math.min(...mids) > 3) problems.push('items are not on one row');
  if (getComputedStyle(q('.topnav')).position !== 'sticky') problems.push('header is not sticky');
  if (getComputedStyle(q('.topnav__nav')).position === 'fixed') problems.push('links detached from the bar');
  if (document.documentElement.scrollWidth > innerWidth) problems.push('page scrolls sideways');
  return problems;
}"""


@pytest.mark.parametrize("path", ["/", "/outreach"])
@pytest.mark.parametrize("width", [1920, 1536, 1366, 1280, 1100, 1024, 900, 820, 769, 768, 640, 560, 480, 440, 390, 360, 320])
def test_navbar_is_a_single_tidy_row_at_every_window_width(live, page, path, width):
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(live + path)
    page.wait_for_selector("header.topnav")
    page.wait_for_timeout(250)
    assert page.evaluate(BAR_PROBE) == []


@pytest.mark.parametrize("path", ["/", "/outreach"])
def test_navbar_stays_fixed_to_the_top_while_scrolling(live, page, path):
    page.set_viewport_size({"width": 1366, "height": 700})
    page.goto(live + path)
    page.wait_for_selector("header.topnav")
    for y in (400, 1500, 4000):
        page.evaluate(f"window.scrollTo(0, {y})")
        page.wait_for_timeout(120)
        box = page.locator("header.topnav").bounding_box()
        assert box["y"] == 0 and box["height"] > 50, f"navbar moved at scroll {y}"
        assert page.evaluate("document.elementFromPoint(300, 30).closest('header.topnav') !== null"), "something covers the navbar"


def test_browser_can_never_mix_new_markup_with_a_stale_stylesheet(live, page):
    """Regression for 'the UI didn't change': assets are versioned URLs and revalidated every time."""
    seen = []
    page.on("response", lambda r: seen.append((r.url, r.headers.get("cache-control"))) if "/static/" in r.url else None)
    page.goto(live + "/")
    page.wait_for_selector(JOBS)
    assets = [(u, c) for u, c in seen if u.endswith((".css", ".js")) or ".css?" in u or ".js?" in u]
    assert len(assets) >= 3 and all(re.search(r"\?v=[0-9a-f]{10}$", u) for u, _ in assets), assets
    assert all(c == "no-cache" for _, c in assets)
    build = page.locator("header.topnav").get_attribute("data-ui-build")
    assert re.fullmatch(r"[0-9a-f]{8}", build) and build in page.get_by_role("link", name=f"{APP_NAME} home").get_attribute("title")


# --------------------------------------------- "Active" means still-to-do: new + saved. Applied and dismissed have their own tabs.
def test_saved_jobs_stay_in_active_while_applied_and_dismissed_do_not(app):
    page = app
    row(page, "Frontend Intern").get_by_test_id("save").click()
    expect(page.get_by_test_id("toast")).to_contain_text("Saved")
    assert "Frontend Intern" in titles(page)                                            # saved: still something to act on
    row(page, "SDE I - Backend").get_by_test_id("mark-applied").click()
    row(page, "Full Stack Developer (0-2 years)").get_by_test_id("dismiss").click()
    expect(row(page, "SDE I - Backend")).to_have_count(0)
    expect(row(page, "Full Stack Developer (0-2 years)")).to_have_count(0)
    assert "Frontend Intern" in titles(page)
    wait_count(page, r"Showing 28 of 37 jobs")                                          # total 39 - 2 = 37; the 2 acted-on rows left the 30 on screen


def test_unmarking_an_applied_job_brings_it_back_to_active(app):
    page = app
    row(page, "SDE I - Backend").get_by_test_id("mark-applied").click()
    page.get_by_test_id("tab-applied").click()
    row(page, "SDE I - Backend").get_by_test_id("mark-applied").click()                  # un-mark
    expect(page.locator(JOBS)).to_have_count(0)                                         # left the Applied tab...
    page.get_by_test_id("tab-all").click()
    expect(row(page, "SDE I - Backend")).to_be_visible()                                # ...and is back in Active
    expect(page.get_by_test_id("tab-applied").locator(".tab__count")).to_have_text("")


def test_search_and_filters_do_not_resurrect_applied_jobs_in_active(app):
    page = app
    row(page, "Frontend Intern").get_by_test_id("mark-applied").click()
    page.get_by_test_id("search").fill("Frontend Intern")
    expect(page.get_by_text("No jobs match these filters")).to_be_visible()
    page.get_by_test_id("tab-applied").click()
    expect(page.locator(JOBS)).to_have_count(1)
    assert titles(page) == ["Frontend Intern"]


# ------------------------------------------------------------------ the match score explains itself and no longer saturates
def test_every_score_ring_is_labelled_and_has_a_legend(app):
    page = app
    box = page.locator(".scorebox").first
    expect(box.locator(".scorebox__label")).to_have_text("match")
    assert "out of 100" in box.get_attribute("title") and "fits your resume" in box.get_attribute("title")
    expect(box.get_by_test_id("score")).to_have_attribute("aria-label", re.compile(r"Match score \d+ out of 100"))
    legend = page.get_by_test_id("score-legend")
    expect(legend).to_be_visible()
    for text in ("how well a job fits your resume", "80+ great", "60–79 good", "40–59 fair", "below 40", "Open a job to see how its score is made"):
        expect(legend).to_contain_text(text)
    colours = legend.locator(".dot").evaluate_all("els => els.map(e => getComputedStyle(e).backgroundColor)")
    assert colours == ["rgb(89, 212, 153)", "rgb(87, 193, 255)", "rgb(255, 197, 51)", "rgb(163, 168, 177)"]      # same colours as the rings


def test_scores_spread_out_instead_of_piling_up_at_100(app):
    """The fixture has 35 identical filler jobs (they must tie), plus 4 hand-written ones that should all differ."""
    page = app
    page.get_by_role("button", name="Load more").click()
    expect(page.locator(JOBS)).to_have_count(39)
    by_title = dict(zip(titles(page), [int(s) for s in page.get_by_test_id("score").evaluate_all("els => els.map(e => e.firstChild.textContent)")]))
    assert max(by_title.values()) < 100, "a strong match is no longer automatically a perfect 100"
    named = {t: by_title[t] for t in ("Full Stack Developer (0-2 years)", "SDE I - Backend", "Frontend Intern")}
    filler = {by_title[t] for t in by_title if t.startswith("Software Engineer ")}
    assert len(filler) == 1                                                             # identical jobs -> identical score (deterministic)
    assert len(set(named.values()) | filler) >= 3, (named, filler)                      # genuinely different jobs -> different scores
    assert named["Full Stack Developer (0-2 years)"] > list(filler)[0]                  # better fit (more skills, coverage, role) ranks above


def test_job_drawer_shows_how_the_score_is_made(app):
    page = app
    row(page, "Full Stack Developer (0-2 years)").get_by_test_id("job-title").click()
    drawer = page.get_by_test_id("job-drawer")
    expect(drawer.get_by_role("heading", name="How the score is made")).to_be_visible()
    rows = drawer.locator(".breakdown__row")
    expect(rows).to_have_count(4)
    expect(rows.locator("span").first).to_have_text("Skills")
    values = drawer.locator(".breakdown__val").all_inner_texts()
    assert [v.split(" / ")[1] for v in values] == ["50", "25", "20", "5"]
    points = [float(v.split(" / ")[0]) for v in values]
    shown = int(drawer.locator("dd").filter(has_text="/ 100").first.inner_text().split(" / ")[0])
    assert round(sum(points)) == shown                                                   # what the bars add up to is the score shown
    expect(drawer.get_by_test_id("score-cap")).to_have_count(0)
    bars = drawer.locator(".breakdown__bar i").evaluate_all("els => els.map(e => e.style.width)")
    assert bars[2] == "100%" and bars[3] == "100%"                                        # entry-level + location are full marks here


def test_a_capped_job_says_why_its_score_is_low(app):
    page = app
    page.goto(page.base + "/?min=0&q=Senior+Staff")
    page.wait_for_selector(JOBS)
    row(page, "Senior Staff Engineer").get_by_test_id("job-title").click()
    cap = page.get_by_test_id("job-drawer").get_by_test_id("score-cap")
    expect(cap).to_contain_text("Capped at 25")
    expect(cap).to_contain_text("seniority")


# ------------------------------------------------ layout geometry (regression: a stray </div> pushed the whole list below the sidebar)
def test_results_sit_beside_the_filters_not_below_them(app):
    page = app
    geo = page.evaluate("""() => { const r = s => { const b = document.querySelector(s).getBoundingClientRect(); return {x: b.x, y: b.y, w: b.width, b: b.y + b.height} };
      return {filters: r('#filters'), results: r('#results'), list: r('#joblist'), parent: document.querySelector('#results').parentElement.className} }""")
    assert geo["parent"] == "layout"
    assert geo["results"]["x"] > geo["filters"]["x"] + geo["filters"]["w"] - 1, "results are to the right of the filters"
    assert abs(geo["results"]["y"] - geo["filters"]["y"]) < 2, "...and start on the same row"
    assert geo["list"]["y"] < 900, "the first job is visible without scrolling past the whole sidebar"
    assert geo["results"]["w"] > 600


def test_every_filter_group_stays_inside_the_sidebar(app):
    page = app
    inside = page.evaluate("""() => { const sb = document.querySelector('#filters').getBoundingClientRect();
      return [...document.querySelectorAll('#filters .field, #filters fieldset')].map(e => { const b = e.getBoundingClientRect(); return b.x >= sb.x - 1 && b.right <= sb.right + 1 }) }""")
    assert inside and all(inside)
    assert page.locator("#filters > *").count() >= 7


@pytest.mark.parametrize("width", [1366, 1100, 1024, 820, 390])
def test_main_page_regions_keep_their_structure_at_every_width(live, page, width):
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(live + "/")
    page.wait_for_selector(JOBS)
    parents = page.evaluate("[...document.querySelectorAll('#filters, #results')].map(e => e.parentElement.className)")
    assert parents == ["layout", "layout"]
    results = page.evaluate("(() => { const b = document.querySelector('#results').getBoundingClientRect(); return [b.x, b.width] })()")
    assert results[1] > min(300, width - 40) and results[0] >= 0
