"""Asset versioning + revalidation (the fix for 'I restarted and the UI still looks old')."""
import re

from fastapi.testclient import TestClient

from app import assets
from app.main import create_app


def test_version_is_a_content_hash_that_changes_with_the_file(tmp_path):
    f = tmp_path / "a.css"
    f.write_text("body{}")
    v1 = assets.version("a.css", tmp_path)
    assert re.fullmatch(r"[0-9a-f]{10}", v1) and assets.version("a.css", tmp_path) == v1
    f.write_text("body{color:red}")
    assert assets.version("a.css", tmp_path) != v1
    assert assets.version("missing.css", tmp_path) == "0"


def test_finalize_versions_local_assets_only_and_fills_the_build_id(tmp_path):
    (tmp_path / "x.css").write_text("a{}")
    (tmp_path / "y.js").write_text("1")
    html = '<link href="/static/x.css"><script src="/static/y.js"></script><img src="/static/logo.png"><a href="https://cdn.example/x.css">__UI_BUILD__</a>'
    out = assets.finalize(html, tmp_path)
    assert re.search(r'href="/static/x\.css\?v=[0-9a-f]{10}"', out) and re.search(r'src="/static/y\.js\?v=[0-9a-f]{10}"', out)
    assert 'src="/static/logo.png"' in out and 'href="https://cdn.example/x.css"' in out       # untouched
    assert "__UI_BUILD__" not in out and re.search(r">[0-9a-f]{8}<", out)
    (tmp_path / "x.css").write_text("a{color:red}")
    assert assets.build_id(tmp_path) != re.search(r">([0-9a-f]{8})<", out).group(1)              # any asset change changes the build id


def test_pages_and_static_files_are_never_served_from_a_stale_cache(store):
    c = TestClient(create_app(store, crawler=lambda s: {}))
    for path in ("/", "/outreach"):
        r = c.get(path)
        assert r.headers["cache-control"] == "no-cache"
        urls = re.findall(r'(?:href|src)="(/static/[^"]+)"', r.text)
        assert urls and all(re.search(r"\?v=[0-9a-f]{10}$", u) for u in urls), urls
        for u in urls:
            s = c.get(u)
            assert s.status_code == 200 and s.headers["cache-control"] == "no-cache" and s.headers.get("etag")
            assert c.get(u, headers={"If-None-Match": s.headers["etag"]}).status_code == 304        # revalidation is cheap
        assert re.search(r'data-ui-build="[0-9a-f]{8}"', r.text) and "__UI_BUILD__" not in r.text
