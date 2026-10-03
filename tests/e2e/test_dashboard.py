"""End-to-end tests of the dashboard in a real browser (Chromium via Playwright).

Seeded data (see server.py): 5 named jobs + 35 "Software Engineer NN" fillers; default view is Match >= 50.
"""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

JOBS = "[data-testid=job]"
TITLES = "[data-testid=job-title]"


def row(page, title):
    return page.locator(JOBS).filter(has=page.get_by_role("button", name=title, exact=True))


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
    expect(page).to_have_title(re.compile("Jobs"))
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


def test_mark_applied_persists_across_reload(app):
    page = app
    row(page, "SDE I - Backend").get_by_test_id("mark-applied").click()
    expect(row(page, "SDE I - Backend").locator(".badge--applied")).to_have_text("Applied")
    page.reload()
    page.wait_for_selector(JOBS)
    expect(row(page, "SDE I - Backend").locator(".badge--applied")).to_be_visible()
    page.get_by_test_id("tab-applied").click()
    expect(page.locator(JOBS)).to_have_count(1)
    assert titles(page) == ["SDE I - Backend"]
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


def test_mark_applied_from_drawer_updates_row(app):
    page = app
    page.get_by_role("button", name="Frontend Intern", exact=True).click()
    page.get_by_test_id("job-drawer").get_by_role("button", name="Mark applied").click()
    expect(page.get_by_test_id("job-drawer").get_by_role("button", name="Unmark applied")).to_be_visible()
    expect(row(page, "Frontend Intern").locator(".badge--applied")).to_be_visible()


# ---------------------------------------------------------------------- profile
def test_profile_edit_rescoring_hides_excluded_titles(app):
    page = app
    page.get_by_test_id("open-profile").click()
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
    page.get_by_test_id("open-profile").click()
    page.locator("#p-loc-remote").uncheck()
    page.get_by_test_id("save-profile").click()
    expect(page.get_by_test_id("toast")).to_contain_text("re-matched")
    assert page.request.get(page.base + "/api/profile").json()["locations"] == ["india"]
    page.get_by_test_id("open-profile").click()
    expect(page.locator("#p-loc-remote")).not_to_be_checked()
    expect(page.locator("#p-loc-india")).to_be_checked()


def test_resume_upload_replaces_profile(app, tmp_path):
    page = app
    cv = tmp_path / "newcv.txt"
    cv.write_text("Sam Rivera\nBackend Developer | Python • Django\nSkills: Python, Django, PostgreSQL, Docker, AWS, Redis\n"
                  "Built Django REST APIs with Python and PostgreSQL. Python Django Python.\nBE 2022 - 2026")
    page.get_by_test_id("open-profile").click()
    page.get_by_test_id("resume-input").set_input_files(str(cv))
    expect(page.get_by_test_id("toast")).to_contain_text("Resume processed")
    expect(page.get_by_role("heading", level=1)).to_have_text("Jobs for Sam")
    assert "django" in page.request.get(page.base + "/api/profile").json()["skills"]


def test_resume_upload_rejects_bad_file_with_message(app, tmp_path):
    page = app
    bad = tmp_path / "notes.txt"
    bad.write_text("nothing recognisable in here, only prose about hiking and gardening")
    page.get_by_test_id("open-profile").click()
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


def test_salary_filter_min_lpa_with_chip_and_url(app):
    page = app
    hint = page.get_by_test_id("salary-hint")
    expect(hint).to_contain_text("3 of 41 jobs list a salary")
    page.get_by_test_id("lpa").select_option("10")
    wait_count(page, r"Showing 2 of 2 jobs$")
    assert set(titles(page)) == {"Full Stack Developer (0-2 years)", "SDE I - Backend"}       # unlisted jobs are excluded
    assert "lpa=10" in page.url
    page.get_by_test_id("lpa").select_option("25")
    wait_count(page, r"Showing 1 of 1 job$")
    assert titles(page) == ["SDE I - Backend"]
    chip = page.locator("[data-testid=chip]").filter(has_text="₹25 LPA+")
    expect(chip).to_be_visible()
    chip.get_by_role("button").click()
    expect(page.get_by_test_id("lpa")).to_have_value("")
    wait_count(page, r"of 39 jobs$")


def test_salary_listed_filter_and_sort_highest(app):
    page = app
    page.get_by_test_id("lpa").select_option("listed")
    wait_count(page, r"Showing 3 of 3 jobs$")
    page.get_by_test_id("sort").select_option("salary")
    expect(page.locator(TITLES).first).to_have_text("SDE I - Backend")
    assert titles(page) == ["SDE I - Backend", "Full Stack Developer (0-2 years)", "Frontend Intern"]
    assert "sort=salary" in page.url


def test_salary_filter_restored_from_url_and_reset(app):
    page = app
    page.goto(f"{page.base}/?lpa=15&min=0")
    page.wait_for_selector(JOBS)
    expect(page.get_by_test_id("lpa")).to_have_value("15")
    assert titles(page) == ["SDE I - Backend", "Full Stack Developer (0-2 years)"] or set(titles(page)) == {"SDE I - Backend", "Full Stack Developer (0-2 years)"}
    page.get_by_test_id("clear-filters").click()
    expect(page.get_by_test_id("lpa")).to_have_value("")


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
