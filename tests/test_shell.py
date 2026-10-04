"""The shared app shell (brand + navigation) rendered on both pages."""
from html.parser import HTMLParser

import pytest

from app import config, shell


def parse(html):
    class P(HTMLParser):
        def __init__(self):
            super().__init__(); self.tags = []
        def handle_starttag(self, tag, attrs):
            self.tags.append((tag, dict(attrs)))
    p = P(); p.feed(html)
    return p.tags


def find(tags, name, **attrs):
    return [a for t, a in tags if t == name and all(a.get(k) == v for k, v in attrs.items())]


@pytest.mark.parametrize("active,current", [("jobs", "/"), ("outreach", "/outreach")])
def test_exactly_one_link_is_current_and_it_is_the_active_page(active, current):
    tags = parse(shell.nav_html(active))
    links = find(tags, "a", **{"class": "navlink"})
    assert [l["href"] for l in links] == ["/", "/outreach"]
    assert [l["href"] for l in links if l.get("aria-current") == "page"] == [current]


def test_brand_link_name_contains_its_visible_text_and_nav_is_labelled():
    tags = parse(shell.nav_html("jobs"))
    (brand,) = find(tags, "a", **{"class": "brand"})
    assert brand["href"] == "/" and config.APP_NAME in brand["aria-label"]        # WCAG 2.5.3: label in name
    assert find(tags, "nav", **{"aria-label": "Primary", "data-testid": "nav"})
    assert all(t != "script" for t, _ in tags)                                    # static markup only


def test_icons_are_decorative_and_every_control_has_text():
    html = shell.nav_html("jobs")
    assert html.count("<svg") == 5 and html.count("<svg") == html.count('aria-hidden="true" focusable="false"')
    tags = parse(html)
    for btn_id in ("refresh", "open-profile", "filters-toggle"):
        assert find(tags, "button", id=btn_id)
    for label in ("Refresh jobs", "Profile", "Filters"):
        assert label in html                                                       # icon-only on phones, but the text is in the DOM


def test_jobs_and_outreach_have_different_right_hand_sides():
    jobs, out = shell.nav_html("jobs"), shell.nav_html("outreach")
    assert 'id="refresh"' in jobs and 'id="run-meta"' in jobs and "Nothing is sent automatically" not in jobs
    assert "Nothing is sent automatically" in out and 'id="refresh"' not in out and 'id="open-profile"' not in out


def test_unknown_page_is_rejected():
    with pytest.raises(ValueError):
        shell.nav_html("admin")



def test_product_name_comes_from_one_setting_and_is_what_people_see(monkeypatch):
    monkeypatch.setattr(config, "APP_NAME", "Acme Jobs")
    html = shell.nav_html("jobs")
    assert 'aria-label="Acme Jobs home"' in html and 'title="Acme Jobs · UI build' in html
    assert '<span class="brand__name">Acme <span>Jobs</span></span>' in html                   # two-tone wordmark
    monkeypatch.setattr(config, "APP_NAME", "Foothold")
    assert '<span class="brand__name">Foothold</span>' in shell.nav_html("outreach")            # one word: all bold


def test_the_name_is_escaped_in_markup(monkeypatch):
    monkeypatch.setattr(config, "APP_NAME", "<b>x</b> & y")                                     # even if a bad value got past _clean_name
    html = shell.nav_html("jobs")
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html


@pytest.mark.parametrize("raw,expected", [
    ("Foothold", "Foothold"), ("  Acme  Jobs ", "Acme  Jobs"), ("A&B Careers", "A&B Careers"), ("Nest-Work 2", "Nest-Work 2"),
    ('<script>alert(1)</script>', "scriptalert1script"), ('x"onmouseover="y', "xonmouseovery"), ("", "Foothold"), ("   ", "Foothold"), ("<>", "Foothold"),
    ("N" * 80, "N" * 30),
])
def test_app_name_is_sanitised_and_bounded(raw, expected):
    assert config._clean_name(raw) == expected


def test_jobs_page_title_and_brand_use_the_configured_name(store, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    monkeypatch.setattr(config, "APP_NAME", "Acme & Co")
    html = TestClient(create_app(store, crawler=lambda s: {})).get("/").text
    assert "<title>Acme &amp; Co — jobs matched to your resume</title>" in html and "__APP_NAME__" not in html
    assert 'aria-label="Acme &amp; Co home"' in html
