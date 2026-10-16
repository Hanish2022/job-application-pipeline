"""Dismiss reasons + learning, hiding staffing agencies, old / possibly-closed postings, skill gaps (real browser).

Uses the 'feedback' seed (tests/e2e/server.py): on top of the usual jobs there is a staffing agency (Saral Recruitments), a posting
200 days old (Ancient Co), one not seen for 20 days (Vanished Co), two Django jobs (Django Shop) + one more Django job (Target Co),
and three ordinary jobs that all ask for TypeScript and Docker (Gap Co 0-2) - which the seeded resume does not mention.
"""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

JOBS = "[data-testid=job]"


def card(page, company):
    return page.locator(JOBS).filter(has_text=company).first


def cards(page, company):
    return page.locator(JOBS).filter(has_text=company)


def toast(page):
    return page.get_by_test_id("toast")


def dismiss(page, company, nth=0):
    cards(page, company).nth(nth).get_by_test_id("dismiss").click()
    expect(toast(page)).to_contain_text("Dismissed")


def score_of(page, company) -> int:
    return int(card(page, company).get_by_test_id("score").inner_text())


def open_profile(page):
    page.get_by_test_id("open-profile").click()
    expect(page.get_by_test_id("profile-drawer")).to_be_visible()


# ===================================================================================== dismiss reasons
def test_dismissing_still_takes_one_click_and_then_offers_reasons(fb):
    page = fb
    dismiss(page, "Gap Co 0")
    assert cards(page, "Gap Co 0").count() == 0                                          # gone immediately, no dialog in the way
    labels = toast(page).get_by_role("group", name="Reason").get_by_role("button").all_inner_texts()
    assert labels == ["Wrong tech stack", "Too senior", "Location", "Salary", "Staffing agency", "Hide this company"]


def test_a_reason_is_saved_and_shown_in_the_dismissed_tab_and_forgotten_on_restore(fb):
    page = fb
    dismiss(page, "Gap Co 0")
    toast(page).get_by_test_id("reason-too_senior").click()
    expect(toast(page)).to_have_text("Noted.")
    page.get_by_test_id("tab-dismissed").click()
    expect(page.get_by_test_id("reason-tag")).to_have_text("Dismissed: too senior")
    page.get_by_role("button", name=re.compile("^Restore")).click()
    page.get_by_test_id("tab-all").click()
    expect(card(page, "Gap Co 0")).to_be_visible()
    assert page.request.get(page.base + "/api/jobs?status=new&company=Gap%20Co%200&min_score=0").json()["items"][0]["dismiss_reason"] == ""


def test_the_reason_buttons_wait_while_you_hover_or_focus_them(fb):
    page = fb
    dismiss(page, "Gap Co 0")
    page.get_by_test_id("reason-salary").focus()
    page.wait_for_timeout(100)
    expect(toast(page)).to_be_visible()                                                   # still there, with focus on a button
    toast(page).get_by_test_id("reason-salary").press("Enter")                            # works from the keyboard
    expect(toast(page)).to_have_text("Noted.")


def test_hide_this_company_hides_all_its_jobs_and_can_be_undone_in_profile(fb):
    page = fb
    assert cards(page, "Django Shop").count() == 2
    dismiss(page, "Django Shop")
    toast(page).get_by_test_id("reason-company").click()
    expect(toast(page)).to_contain_text("Hiding Django Shop")
    expect(cards(page, "Django Shop")).to_have_count(0)                                   # the OTHER Django Shop job is gone too
    open_profile(page)
    expect(page.get_by_test_id("blocked-companies").get_by_test_id("flag")).to_have_text("Django Shop")
    page.get_by_role("button", name="Stop hiding Django Shop").click()
    expect(page.get_by_test_id("blocked-companies")).to_contain_text("None")
    page.get_by_role("button", name="Close profile").click()
    expect(cards(page, "Django Shop")).to_have_count(1)                                   # the dismissed one stays dismissed; its sibling is back


def test_a_company_can_be_hidden_by_name_from_profile(fb):
    page = fb
    open_profile(page)
    page.locator("#p-block-input").fill("Target Co")
    page.locator("#p-block-form").get_by_role("button", name="Hide").click()
    expect(page.get_by_test_id("blocked-companies")).to_contain_text("Target Co")
    page.get_by_role("button", name="Close profile").click()
    expect(cards(page, "Target Co")).to_have_count(0)


def test_company_names_are_shown_as_text_not_markup(fb):
    page = fb
    evil = '<img src=x onerror="window.__xss=1">'
    assert page.request.post(page.base + "/api/companies/flags", data={"company": evil, "kind": "blocked"}).status == 201
    open_profile(page)
    expect(page.get_by_test_id("blocked-companies").get_by_test_id("flag")).to_have_text(evil)
    assert page.evaluate("window.__xss") is None and page.locator("#p-blocked img").count() == 0


def test_dismissing_as_staffing_agency_flags_the_company(fb):
    page = fb
    dismiss(page, "Gap Co 1")
    toast(page).get_by_test_id("reason-agency").click()
    expect(toast(page)).to_contain_text("Marked Gap Co 1 as a staffing agency")
    open_profile(page)
    expect(page.get_by_test_id("agency-companies").get_by_test_id("flag")).to_have_text("Gap Co 1")


def test_dismiss_from_the_job_drawer_also_asks_why(fb):
    page = fb
    card(page, "Gap Co 2").get_by_test_id("job-title").click()
    expect(page.get_by_test_id("job-drawer")).to_be_visible()
    page.get_by_test_id("drawer-dismiss").click()
    expect(page.get_by_test_id("job-drawer")).to_be_hidden()
    expect(toast(page).get_by_test_id("reason-location")).to_be_visible()
    assert cards(page, "Gap Co 2").count() == 0


# ===================================================================================== learning
def test_two_wrong_stack_dismissals_lower_the_score_of_similar_jobs_and_explain_it(fb):
    page = fb
    before = score_of(page, "Target Co")
    for _ in range(2):
        dismiss(page, "Django Shop")
        toast(page).get_by_test_id("reason-wrong_stack").click()
    expect(toast(page)).to_contain_text("score lower")
    expect(card(page, "Target Co").get_by_test_id("score")).not_to_have_text(str(before))     # the list reloaded with the new scores
    assert score_of(page, "Target Co") < before
    card(page, "Target Co").get_by_test_id("job-title").click()
    avoid = page.get_by_test_id("score-avoid")
    expect(avoid).to_contain_text("django")
    expect(avoid).to_contain_text("wrong tech stack")
    expect(page.get_by_test_id("job-drawer")).to_contain_text("Uses technologies you passed on")


def test_one_wrong_stack_dismissal_teaches_nothing_yet(fb):
    page = fb
    before = score_of(page, "Target Co")
    dismiss(page, "Django Shop")
    toast(page).get_by_test_id("reason-wrong_stack").click()
    expect(toast(page)).to_contain_text("Once two jobs")
    assert score_of(page, "Target Co") == before


def test_restoring_a_job_undoes_what_it_taught(fb):
    page = fb
    before = score_of(page, "Target Co")
    for _ in range(2):
        dismiss(page, "Django Shop")
        toast(page).get_by_test_id("reason-wrong_stack").click()
    expect(toast(page)).to_contain_text("score lower")
    page.get_by_test_id("tab-dismissed").click()
    page.get_by_role("button", name=re.compile("^Restore")).first.click()
    page.get_by_test_id("tab-all").click()
    expect(card(page, "Target Co")).to_be_visible()
    expect(page.get_by_test_id("job")).not_to_have_count(0)
    assert page.request.get(page.base + "/api/jobs?company=Target%20Co&min_score=0").json()["items"][0]["score"] == before


# ===================================================================================== staffing agencies
def test_staffing_agencies_are_hidden_by_default_counted_and_can_be_shown(fb):
    page = fb
    expect(page.get_by_test_id("hide-agencies")).to_be_checked()
    assert cards(page, "Saral Recruitments").count() == 0
    expect(page.get_by_test_id("hidden-note")).to_contain_text("1 staffing agency job hidden")
    expect(page.get_by_test_id("hidden-agencies")).to_have_text("1")
    page.get_by_test_id("show-agencies").click()
    expect(card(page, "Saral Recruitments")).to_be_visible()
    expect(card(page, "Saral Recruitments").get_by_test_id("agency-tag")).to_have_text("Staffing")
    expect(card(page, "Saral Recruitments").get_by_test_id("agency-tag")).to_have_attribute("title", re.compile("name says"))
    expect(page.get_by_test_id("hide-agencies")).not_to_be_checked()
    expect(page.get_by_test_id("chip").filter(has_text="Showing staffing agencies")).to_be_visible()
    assert "agencies=1" in page.url


def test_the_agency_switch_survives_a_reload_and_the_chip_turns_it_back_off(fb):
    page = fb
    page.goto(page.base + "/?agencies=1")
    expect(card(page, "Saral Recruitments")).to_be_visible()
    expect(page.get_by_test_id("hide-agencies")).not_to_be_checked()
    page.get_by_test_id("chip").filter(has_text="Showing staffing agencies").get_by_role("button").click()
    expect(page.get_by_test_id("hide-agencies")).to_be_checked()
    expect(cards(page, "Saral Recruitments")).to_have_count(0)
    assert "agencies" not in page.url


def test_ticking_the_box_hides_them_again(fb):
    page = fb
    page.get_by_test_id("hide-agencies").uncheck()
    expect(card(page, "Saral Recruitments")).to_be_visible()
    page.get_by_test_id("hide-agencies").check()
    expect(cards(page, "Saral Recruitments")).to_have_count(0)


def test_a_job_you_saved_is_never_tidied_away(fb):
    page = fb
    page.goto(page.base + "/?agencies=1&old=1")
    card(page, "Saral Recruitments").get_by_test_id("save").click()
    card(page, "Ancient Co").get_by_test_id("save").click()
    page.goto(page.base + "/")
    expect(card(page, "Saral Recruitments")).to_be_visible()
    expect(card(page, "Ancient Co")).to_be_visible()
    note = page.get_by_test_id("hidden-note")
    expect(note).to_contain_text("1 old or possibly closed posting hidden")               # only the one you did NOT save (Vanished Co)
    expect(note).not_to_contain_text("staffing")
    assert cards(page, "Vanished Co").count() == 0


# ===================================================================================== old / possibly closed
def test_old_and_possibly_closed_postings_are_hidden_by_default_with_a_visible_count(fb):
    page = fb
    for company in ("Ancient Co", "Vanished Co"):
        assert cards(page, company).count() == 0
    expect(page.get_by_test_id("hidden-note")).to_contain_text("2 old or possibly closed postings hidden")
    expect(page.get_by_test_id("hidden-old")).to_have_text("2")
    page.get_by_test_id("show-old").click()
    expect(card(page, "Ancient Co")).to_be_visible()
    expect(card(page, "Ancient Co").get_by_test_id("old-tag")).to_have_text("Old posting")
    expect(card(page, "Ancient Co").get_by_test_id("age")).to_have_class(re.compile("age--old"))
    expect(card(page, "Vanished Co").get_by_test_id("closed-tag")).to_have_text("May be closed")
    expect(card(page, "Vanished Co").get_by_test_id("old-tag")).to_have_count(0)           # posted 3 days ago: not "old", just unseen
    expect(page.get_by_test_id("hidden-note")).not_to_contain_text("old")                  # the agency job is still hidden, so that part of the note stays
    expect(page.get_by_test_id("hidden-old")).to_have_text("")
    assert "old=1" in page.url


def test_every_card_shows_how_old_the_posting_is(fb):
    page = fb
    page.get_by_test_id("hide-old").uncheck()
    ages = card(page, "Ancient Co").get_by_test_id("age")
    expect(ages).to_have_text(re.compile(r"^\d+mo ago$"))
    expect(ages).to_have_attribute("title", re.compile(r"^Posted \d{4}-\d{2}-\d{2}$"))
    expect(card(page, "Gap Co 0").get_by_test_id("age")).to_have_text(re.compile(r"^\d+[hd] ago$"))


def test_the_tidy_switches_do_not_touch_the_other_tabs(fb):
    page = fb
    page.get_by_test_id("hide-old").uncheck()
    card(page, "Ancient Co").get_by_test_id("mark-applied").click()
    page.get_by_test_id("hide-old").check()
    page.get_by_test_id("tab-applied").click()
    expect(card(page, "Ancient Co")).to_be_visible()                                       # an applied job stays visible in its tab
    expect(page.get_by_test_id("hidden-note")).to_be_hidden()                              # nothing is "hidden" on that tab


def test_sidebar_counts_agree_with_what_the_list_can_show(fb):
    page = fb
    n_hidden = int(page.get_by_test_id("hidden-old").inner_text()) + int(page.get_by_test_id("hidden-agencies").inner_text())
    assert n_hidden == 3
    default_total = int(re.search(r"of (\d+)", page.get_by_test_id("count").inner_text()).group(1))
    page.get_by_test_id("hide-old").uncheck(); page.get_by_test_id("hide-agencies").uncheck()
    expect(page.get_by_test_id("count")).to_contain_text(f"of {default_total + 3} job")


# ===================================================================================== skill gaps
def test_skill_gaps_lists_what_the_best_matches_want_and_you_lack(fb):
    page = fb
    page.get_by_test_id("open-gaps").click()
    drawer = page.get_by_test_id("gaps-drawer")
    expect(drawer).to_be_visible()
    expect(page.get_by_test_id("gaps-intro")).to_contain_text("best matches")
    names = page.get_by_test_id("gaps-list").locator(".gap__name").all_inner_texts()
    assert "typescript" in names and "docker" in names
    expect(page.locator('.gap[data-skill="typescript"] .gap__n')).to_have_text(re.compile(r"^in 3 of \d+ jobs \(\d+%\)$"))
    expect(page.locator('.gap[data-skill="typescript"]')).to_contain_text("You know javascript, which is close")
    assert "react" not in names and "git" not in names                                       # what you have, and generic words, are not gaps
    expect(page.get_by_test_id("gaps-have")).to_contain_text("react")


def test_i_know_this_adds_the_skill_and_removes_the_gap(fb):
    page = fb
    page.get_by_test_id("open-gaps").click()
    page.locator('.gap[data-skill="typescript"]').get_by_test_id("add-skill").click()
    expect(toast(page)).to_contain_text("Added typescript")
    expect(page.locator('.gap[data-skill="typescript"]')).to_have_count(0)
    expect(page.locator('.gap[data-skill="docker"]')).to_have_count(1)
    expect(page.get_by_test_id("gaps-have")).to_contain_text("typescript")
    assert "typescript" in page.request.get(page.base + "/api/profile").json()["skills"]


def test_skill_gaps_drawer_closes_with_escape_and_returns_focus(fb):
    page = fb
    button = page.get_by_test_id("open-gaps")
    button.click()
    expect(page.get_by_test_id("gaps-drawer")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_test_id("gaps-drawer")).to_be_hidden()
    expect(button).to_be_focused()


def test_skill_gaps_ignore_jobs_you_have_hidden(fb):
    page = fb
    page.get_by_test_id("open-gaps").click()
    before = page.locator('.gap[data-skill="docker"] .gap__n').inner_text()
    page.keyboard.press("Escape")
    page.get_by_test_id("hide-agencies").uncheck()                                          # Saral (an agency) would count if it were not tidied away
    page.get_by_test_id("open-gaps").click()
    assert page.locator('.gap[data-skill="docker"] .gap__n').inner_text() == before          # the insight always uses the tidy list


# ===================================================================================== layout + accessibility
@pytest.mark.parametrize("width", [1366, 1024, 768, 390, 320])
def test_new_controls_fit_at_every_width(fb, width):
    page = fb
    page.set_viewport_size({"width": width, "height": 800})
    dismiss(page, "Gap Co 0")
    toast_box = toast(page).bounding_box()
    assert toast_box["x"] >= 0 and toast_box["x"] + toast_box["width"] <= width + 0.5, "reason buttons stay inside the screen"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "no sideways scrolling"
    gaps = page.get_by_test_id("open-gaps").bounding_box()
    assert gaps and gaps["x"] >= 0 and gaps["x"] + gaps["width"] <= width + 0.5
    assert page.evaluate("[...document.querySelectorAll('.toast__btn')].every(b => b.getBoundingClientRect().height >= 26)")


def test_the_tidy_checkboxes_have_clean_accessible_names(fb):
    page = fb
    page.set_viewport_size({"width": 1366, "height": 900})
    assert page.get_by_role("checkbox", name="Hide staffing agencies", exact=True).count() == 1       # the count is a description, not part of the name
    assert page.get_by_role("checkbox", name="Hide old postings", exact=True).count() == 1
    assert page.get_by_test_id("hide-agencies").get_attribute("aria-describedby") == "t-agencies-n"


# ===================================================================================== skill gaps: loading, clicking, undo
def hold(page, pattern, method):
    """Park matching requests (so a loading/busy state can be inspected); the test releases them with .continue_()."""
    held = []
    page.route(pattern, lambda route: held.append(route) if route.request.method == method else route.fallback())
    return held


def gap(page, skill):
    return page.locator(f'.gap[data-skill="{skill}"]')


def test_skill_gaps_opens_instantly_with_a_loading_state_then_fills_in(fb):
    page = fb
    held = hold(page, "**/api/insights/skills*", "GET")
    page.get_by_test_id("open-gaps").click()
    expect(page.get_by_test_id("gaps-drawer")).to_be_visible()                           # the panel does not wait for the numbers
    expect(page.get_by_test_id("gaps-loading")).to_be_visible()
    expect(page.get_by_test_id("gaps-body")).to_have_attribute("aria-busy", "true")
    expect(page.get_by_role("status").filter(has_text="Reading your best matches")).to_be_visible()
    while not held:
        page.wait_for_timeout(20)
    held[0].continue_()
    expect(page.get_by_test_id("gaps-list")).to_be_visible()
    expect(page.get_by_test_id("gaps-loading")).to_have_count(0)
    expect(page.get_by_test_id("gaps-body")).to_have_attribute("aria-busy", "false")


def test_skill_gaps_load_failure_offers_try_again(fb):
    page = fb
    state = {"fail": True}
    page.route("**/api/insights/skills*", lambda route: route.fulfill(status=500, body="boom") if state["fail"] else route.fallback())
    page.get_by_test_id("open-gaps").click()
    expect(page.get_by_test_id("gaps-body")).to_contain_text("Couldn’t load this")
    assert len(page.console_errors) == 1 and "500" in page.console_errors[0]             # the browser logged exactly the failure we injected
    page.console_errors.clear()
    state["fail"] = False
    page.get_by_test_id("gaps-retry").click()
    expect(page.get_by_test_id("gaps-list")).to_be_visible()


def test_i_know_this_shows_progress_blocks_double_clicks_then_moves_focus_on(fb):
    page = fb
    page.get_by_test_id("open-gaps").click()
    expect(page.get_by_test_id("gaps-list")).to_be_visible()
    skills = page.locator(".gap").evaluate_all("els => els.map(e => e.dataset.skill)")
    first, second = skills[0], skills[1]
    puts = hold(page, "**/api/profile", "PUT")
    gap(page, first).get_by_test_id("add-skill").click()
    button = gap(page, first).get_by_test_id("add-skill")
    expect(button).to_have_text("Adding…")                                               # visible progress on the button you pressed
    expect(button).to_be_disabled()
    expect(gap(page, second).get_by_test_id("add-skill")).to_be_disabled()               # no second save while one is in flight
    while not puts:
        page.wait_for_timeout(20)
    page.wait_for_timeout(100)
    assert len(puts) == 1
    puts[0].continue_()
    expect(toast(page)).to_contain_text(f"Added {first} to your skills")
    expect(gap(page, first)).to_have_count(0)
    expect(gap(page, second).get_by_test_id("add-skill")).to_be_enabled()
    expect(gap(page, second).get_by_test_id("add-skill")).to_be_focused()                # keyboard users land on the next skill, not on <body>
    expect(page.get_by_test_id("gaps-have")).to_contain_text(first)


def test_undo_takes_the_skill_back_and_the_gap_returns(fb):
    page = fb
    page.get_by_test_id("open-gaps").click()
    expect(gap(page, "typescript")).to_have_count(1)
    gap(page, "typescript").get_by_test_id("add-skill").click()
    expect(toast(page).get_by_role("group", name="Undo")).to_be_visible()
    toast(page).get_by_test_id("undo-skill").click()
    expect(toast(page)).to_contain_text("Removed typescript")
    expect(gap(page, "typescript")).to_have_count(1)                                     # the gap is back in the open panel
    assert "typescript" not in page.request.get(page.base + "/api/profile").json()["skills"]


def test_a_failed_save_leaves_the_button_usable_and_says_so(fb):
    page = fb
    page.get_by_test_id("open-gaps").click()
    expect(gap(page, "docker")).to_have_count(1)
    page.route("**/api/profile", lambda route: route.fulfill(status=500, body="boom") if route.request.method == "PUT" else route.fallback())
    gap(page, "docker").get_by_test_id("add-skill").click()
    expect(toast(page)).to_contain_text("Couldn’t add docker")
    expect(gap(page, "docker").get_by_test_id("add-skill")).to_have_text("I know this")
    expect(gap(page, "docker").get_by_test_id("add-skill")).to_be_enabled()
    assert "docker" not in page.request.get(page.base + "/api/profile").json()["skills"]
    assert len(page.console_errors) == 1 and "500" in page.console_errors[0]
    page.console_errors.clear()


def test_adding_a_skill_keeps_your_place_in_the_list(fb):
    page = fb
    page.set_viewport_size({"width": 1366, "height": 430})                                # short window: the panel must scroll
    page.get_by_test_id("open-gaps").click()
    expect(page.get_by_test_id("gaps-list")).to_be_visible()
    body = page.get_by_test_id("gaps-body")
    body.evaluate("el => { el.scrollTop = 140; }")
    before = body.evaluate("el => el.scrollTop")
    assert before > 0
    target = page.locator(".gap").nth(2)
    skill = target.get_attribute("data-skill")
    target.get_by_test_id("add-skill").click()
    expect(gap(page, skill)).to_have_count(0)
    assert body.evaluate("el => el.scrollTop") > 0, "the panel jumped back to the top"


def test_show_jobs_filters_the_list_by_that_skill_and_closes_the_panel(fb):
    page = fb
    page.get_by_test_id("open-gaps").click()
    gap(page, "docker").get_by_test_id("show-jobs").click()
    expect(page.get_by_test_id("gaps-drawer")).to_be_hidden()
    expect(page.get_by_test_id("search")).to_have_value("docker")
    expect(page.get_by_test_id("chip").filter(has_text="docker")).to_be_visible()
    companies = page.locator(".job__meta span:first-child").all_inner_texts()
    assert companies and all(c.startswith("Gap Co") for c in companies), companies       # only the jobs that mention docker


def test_skill_gaps_buttons_are_big_enough_and_fit_a_phone(fb):
    page = fb
    page.emulate_media(reduced_motion="reduce")                                          # measure the settled layout, not the slide-in
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_test_id("open-gaps").click()
    expect(page.get_by_test_id("gaps-list")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    box = gap(page, "docker").get_by_test_id("add-skill").bounding_box()
    assert box["height"] >= 30 and box["x"] >= 0 and box["x"] + box["width"] <= 390
    drawer = page.get_by_test_id("gaps-drawer").bounding_box()
    assert drawer["x"] >= 0 and drawer["x"] + drawer["width"] <= 390 + 0.5


# ===================================================================================== jobs with no posting date
def test_a_job_with_no_posting_date_says_found_not_seen_or_posted(fb):
    page = fb
    age = card(page, "Undated Co").get_by_test_id("age")
    expect(age).to_have_text(re.compile(r"^found \d+[mh] ago$"))                                 # "found", so nobody reads it as "posted 1 minute ago"
    expect(age).to_have_attribute("title", re.compile("shows no date"))
    card(page, "Undated Co").get_by_test_id("job-title").click()
    expect(page.get_by_test_id("job-drawer")).to_contain_text("no date on the posting")
    expect(card(page, "Gap Co 0").get_by_test_id("age")).to_have_text(re.compile(r"^\d+[hd] ago$"))     # a dated job shows a plain age
