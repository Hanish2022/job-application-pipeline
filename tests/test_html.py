"""Markup must be well-formed: one stray closing tag silently re-parents whole page sections (a real bug we shipped once)."""
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import outreach, shell
from app.main import create_app

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
VOID = {"meta", "link", "input", "br", "img", "hr", "area", "base", "col", "embed", "source", "track", "wbr"}


def problems(html: str) -> list[str]:
    class P(HTMLParser):
        def __init__(self):
            super().__init__(); self.stack, self.errors = [], []
        def handle_starttag(self, tag, attrs):
            if tag not in VOID:
                self.stack.append((tag, self.getpos()[0]))
        def handle_endtag(self, tag):
            if tag in VOID:
                return
            if not self.stack or self.stack[-1][0] != tag:
                self.errors.append(f"line {self.getpos()[0]}: </{tag}> but the innermost open element is {self.stack[-1] if self.stack else None}")
            else:
                self.stack.pop()
    p = P(); p.feed(html); p.close()
    return p.errors + [f"never closed: <{t}> opened on line {ln}" for t, ln in p.stack]


def test_index_html_is_well_formed():
    assert problems((STATIC / "index.html").read_text()) == []


def test_rendered_pages_are_well_formed(store):
    c = TestClient(create_app(store, crawler=lambda s: {}))
    assert problems(c.get("/").text) == []
    assert problems(shell.nav_html("jobs")) == [] and problems(shell.nav_html("outreach")) == []


def test_the_page_skeleton_nests_the_way_the_css_grid_expects():
    """.layout must directly contain the filters sidebar AND the results column."""
    class P(HTMLParser):
        def __init__(self):
            super().__init__(); self.stack, self.parent_of = [], {}
        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if a.get("id") in ("filters", "results"):
                self.parent_of[a["id"]] = (self.stack[-1][1] or "") if self.stack else ""
            if tag not in VOID:
                self.stack.append((tag, a.get("class")))
        def handle_endtag(self, tag):
            if tag not in VOID and self.stack:
                self.stack.pop()
    p = P(); p.feed((STATIC / "index.html").read_text())
    assert p.parent_of == {"filters": "layout", "results": "layout"}, p.parent_of
