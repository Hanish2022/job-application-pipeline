"""The vendored yc-outreach integration: unmodified upstream, our SSRF guard, the themed page, the /api/yc plumbing."""
import hashlib
import http.server
import json
import os
import re
import socket
import subprocess
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, outreach
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "yc-outreach"
UPDATE = ROOT / "scripts" / "update_outreach.sh"


def nav_links(html: str):
    """[(href, label, is_current)] for the primary navigation, read from the real HTML."""
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_nav = False; self.cur = None; self.links = []
        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "nav" and a.get("data-testid") == "nav":
                self.in_nav = True
            elif tag == "a" and self.in_nav:
                self.cur = [a.get("href"), "", a.get("aria-current") == "page"]
        def handle_data(self, data):
            if self.cur is not None:
                self.cur[1] += data
        def handle_endtag(self, tag):
            if tag == "a" and self.cur is not None:
                self.links.append((self.cur[0], self.cur[1].strip(), self.cur[2])); self.cur = None
            elif tag == "nav":
                self.in_nav = False
    p = P(); p.feed(html)
    return p.links


# ---------------------------------------------------------------------- vendored code is really untouched
def test_vendored_files_match_the_recorded_upstream_hashes():
    meta = json.loads((VENDOR / "UPSTREAM.json").read_text())
    assert meta["license"] == "MIT" and re.fullmatch(r"[0-9a-f]{40}", meta["commit"]) and meta["url"].endswith("yc-outreach")
    on_disk = {str(p.relative_to(VENDOR)) for p in VENDOR.rglob("*") if p.is_file() and p.name != "UPSTREAM.json"}
    assert on_disk == set(meta["files"]), "files were added/removed inside vendor/ - vendored code must only change via scripts/update_outreach.sh"
    for name, digest in meta["files"].items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest, f"vendor/{name} was edited by hand"
    assert "MIT License" in (VENDOR / "LICENSE").read_text()


def test_update_script_reviews_then_applies_from_a_local_upstream(tmp_path):
    up = tmp_path / "upstream"
    (up / "api").mkdir(parents=True)
    (up / "index.html").write_text("<html>v1</html>")
    (up / "api" / "yc.py").write_text("# v1\n")
    (up / "LICENSE").write_text("MIT License\n")
    git = lambda *a: subprocess.run(["git", "-C", str(up), *a], check=True, capture_output=True)  # noqa: E731
    git("init", "-q", "-b", "main"); git("add", "."); git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "v1")

    # run the real script against a scratch copy of the project layout so vendor/ in the repo is never touched
    proj = tmp_path / "proj"
    (proj / "scripts").mkdir(parents=True)
    script = proj / "scripts" / "update_outreach.sh"
    script.write_text(UPDATE.read_text()); script.chmod(0o755)
    env = dict(os.environ, UPSTREAM_URL=str(up), JOBS_PYTHON="python3")
    run = lambda *a: subprocess.run([str(script), *a], env=env, capture_output=True, text=True, timeout=60)  # noqa: E731

    r = run()
    assert r.returncode == 0 and "Nothing written" in r.stdout and not (proj / "vendor").exists()        # review mode writes nothing
    assert run("--apply").returncode == 0
    meta = json.loads((proj / "vendor" / "yc-outreach" / "UPSTREAM.json").read_text())
    assert set(meta["files"]) == {"index.html", "api/yc.py", "LICENSE"} and (proj / "vendor/yc-outreach/index.html").read_text() == "<html>v1</html>"

    (up / "api" / "yc.py").write_text("# v2 - now does something new\n")
    git("commit", "-qam", "v2")
    r = run()
    assert "changed files" in r.stdout and "v2" in r.stdout and (proj / "vendor/yc-outreach/api/yc.py").read_text() == "# v1\n"    # shown, not applied
    assert run("--apply").returncode == 0 and (proj / "vendor/yc-outreach/api/yc.py").read_text().startswith("# v2")
    assert run("--bogus").returncode == 2


# ---------------------------------------------------------------------- address policy
@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(ip):
    assert outreach.is_public_ip(ip)


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "127.1.2.3", "10.0.0.1", "172.16.5.5", "192.168.1.1", "169.254.169.254", "0.0.0.0", "100.64.0.1",
    "224.0.0.1", "255.255.255.255", "::1", "fe80::1", "fc00::1", "::",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254", "64:ff9b::7f00:1", "2002:7f00:1::", "not-an-ip", "",
])
def test_non_public_addresses_are_blocked(ip):
    assert not outreach.is_public_ip(ip)


# ---------------------------------------------------------------------- the guard, with real sockets
@pytest.fixture()
def internal():
    """A pretend internal service on loopback, plus a public-looking redirector that bounces to it."""
    hits = []

    class Internal(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200); self.end_headers(); self.wfile.write(b"INTERNAL-SECRET")
        do_POST = do_GET
        def log_message(self, *a): pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Internal)
    port = srv.server_address[1]

    class Redirect(http.server.BaseHTTPRequestHandler):
        target = f"http://127.0.0.1:{port}/admin"
        def do_GET(self):
            self.send_response(302); self.send_header("Location", self.target); self.end_headers()
        def log_message(self, *a): pass

    red = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    for s in (srv, red):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    yield {"port": port, "redir_port": red.server_address[1], "hits": hits, "redirect_cls": Redirect}
    srv.shutdown(); red.shutdown()


@pytest.mark.parametrize("make_url", [
    lambda p, r: f"http://127.0.0.1:{p}/direct",
    lambda p, r: f"http://localhost:{p}/by-name",
    lambda p, r: f"http://[::1]:{p}/v6",
    lambda p, r: f"http://[::ffff:127.0.0.1]:{p}/mapped",
    lambda p, r: f"http://2130706433:{p}/decimal",
    lambda p, r: f"http://0x7f.0.0.1:{p}/hex",
    lambda p, r: f"http://127.1:{p}/short",
    lambda p, r: f"http://127.0.0.1:{r}/",                       # a redirect hop to the internal service
    lambda p, r: "http://169.254.169.254/latest/meta-data/",
    lambda p, r: "http://10.0.0.1/",
])
def test_guarded_get_never_reaches_internal_services(internal, make_url):
    assert outreach.guarded_get(make_url(internal["port"], internal["redir_port"]), timeout=3) is None
    assert internal["hits"] == []                               # nothing arrived at the internal service


def test_only_http_schemes_and_safe_redirects(internal, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET")
    for url in (f"file://{secret}", "ftp://example.com/x", "data:text/plain,hello", "gopher://example.com/"):
        assert outreach.guarded_get(url, timeout=2) is None
    internal["redirect_cls"].target = f"file://{secret}"         # a redirect to a non-http scheme is refused too
    assert outreach.guarded_get(f"http://127.0.0.1:{internal['redir_port']}/", timeout=2) is None


def test_guard_allows_normal_fetches_when_the_address_is_public(internal, monkeypatch):
    monkeypatch.setattr(outreach, "is_public_ip", lambda ip: True)       # pretend loopback is "public" to prove the happy path works
    body = outreach.guarded_get(f"http://127.0.0.1:{internal['port']}/ok", timeout=3, headers={"X-Test": "1"})
    assert body == "INTERNAL-SECRET" and internal["hits"] == ["/ok"]
    data = outreach.guarded_get(f"http://127.0.0.1:{internal['port']}/post", timeout=3, data=b"{}")   # POST (Algolia uses it)
    assert data == "INTERNAL-SECRET" and internal["hits"] == ["/ok", "/post"]


def test_any_private_dns_answer_blocks_the_host_and_dns_is_resolved_once(monkeypatch):
    calls = []

    def fake_getaddrinfo(host, port, *a, **k):
        calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port)), (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(outreach.BlockedAddress):
        outreach._connect_public("mixed.example", 80, 1)             # one bad answer poisons the host (DNS-rebinding style mixes)
    assert calls == ["mixed.example"]

    calls.clear()
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, *a, **k: calls.append(h) or [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.9", p))])
    with pytest.raises(outreach.BlockedAddress):
        outreach._connect_public("doc.example", 80, 1)               # TEST-NET addresses are not global either
    assert calls == ["doc.example"]


def test_loading_the_vendored_module_leaves_vendor_untouched():
    outreach.load_vendor()
    assert not list(VENDOR.rglob("__pycache__")) and not list(VENDOR.rglob("*.pyc"))


def test_vendored_module_uses_the_guarded_fetcher():
    vendor = outreach.load_vendor()
    assert vendor.get.__module__ == outreach.__name__ and vendor.route and vendor.MAX_SLUGS == 10
    assert outreach.load_vendor() is vendor                           # loaded once


# ---------------------------------------------------------------------- the themed page
def test_render_page_is_the_upstream_page_plus_our_shell():
    page = outreach.render_page()
    upstream = (VENDOR / "index.html").read_text()
    assert page.count('data-testid="nav"') == 1 and page.count("/static/shell.css") == 1 and page.count("/static/outreach.css") == 1
    assert f"<title>Cold email — {config.APP_NAME}</title>" in page
    assert nav_links(page) == [("/", "Jobs", False), ("/outreach", "Cold email", True)]
    strip = lambda h: re.sub(r"<title>.*?</title>", "", h, flags=re.S)  # noqa: E731
    stripped = (page.replace(outreach.NAV_HTML, "").replace('<script src="/static/outreach.js" defer></script>', "")
                .replace('<meta name="color-scheme" content="dark">\n<link rel="stylesheet" href="/static/shell.css">\n<link rel="stylesheet" href="/static/outreach.css">', ""))
    assert strip(stripped) == strip(upstream)                         # nothing else about the upstream page changed


def test_render_page_fails_loudly_if_upstream_changes_shape(tmp_path):
    (tmp_path / "index.html").write_text("<html><head></head><body>no title here</body></html>")
    with pytest.raises(RuntimeError, match="changed shape"):
        outreach.render_page(tmp_path)


# ---------------------------------------------------------------------- HTTP surface
@pytest.fixture()
def client(store):
    return TestClient(create_app(store, crawler=lambda s: {}))


def test_outreach_page_and_security_headers(client):
    r = client.get("/outreach")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"] and r.headers["cache-control"] == "no-cache"
    csp = r.headers["content-security-policy"]
    assert "connect-src 'self' https://api.apify.com" in csp and "frame-ancestors 'none'" in csp and "base-uri 'none'" in csp
    assert "object-src" not in csp and "default-src 'self'" in csp
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["referrer-policy"] == "no-referrer"
    assert client.get("/").headers["x-content-type-options"] == "nosniff"      # headers apply app-wide
    assert client.get("/static/outreach.css").status_code == 200 and client.get("/static/shell.css").status_code == 200


def test_outreach_page_503_when_vendor_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr(outreach, "VENDOR_DIR", tmp_path)
    r = client.get("/outreach")
    assert r.status_code == 503 and "unavailable" in r.json()["detail"]


def test_dashboard_has_navigation_to_cold_email(client):
    html = client.get("/").text
    assert nav_links(html) == [("/", "Jobs", True), ("/outreach", "Cold email", False)] and "/static/shell.css" in html
    assert "<!--SHELL_NAV-->" not in html                                        # the placeholder is always replaced


def _fake_network(vendor):
    """Serve YC/Algolia/company-site responses from memory so route() runs for real without any network."""
    company_page = {"props": {"company": {"website": "https://acme.example", "linkedin_url": "https://linkedin.com/company/acme", "twitter_url": "",
        "founders": [{"full_name": "Jane Doe", "title": "CEO", "linkedin_url": "https://linkedin.com/in/jane", "twitter_url": ""},
                     {"full_name": "José Álvarez", "title": "CTO", "linkedin_url": "", "twitter_url": ""}]}}}
    import html as h

    def fake_get(url, timeout=8, data=None, headers=None):
        if url == "https://www.ycombinator.com/companies":
            return 'window.AlgoliaOpts = {"app":"APPID","key":"KEY123"}'
        if "algolia" in url:
            body = json.loads(json.loads(data)["params"] and json.dumps({"p": json.loads(data)["params"]}))["p"]
            if "facets" in body:
                return json.dumps({"facets": {"batch": {"Winter 2024": 3, "Summer 2025": 5, "Unspecified": 1}}})
            return json.dumps({"hits": [{"name": "Acme", "slug": "acme", "batch": "Winter 2024", "website": "https://acme.example",
                                         "one_liner": "Rockets.", "subindustry": "B2B", "team_size": 3, "launched_at": 5}], "nbPages": 1})
        if url == "https://www.ycombinator.com/companies/acme":
            return f'<div data-page="{h.escape(json.dumps(company_page))}"></div>'
        if url.startswith("https://acme.example"):
            return "write us: jane@acme.example or sales@acme.example or ceo@other.com"
        return None

    vendor.get = fake_get
    vendor._algolia = None
    vendor.resolves = lambda d: True


def test_api_yc_end_to_end_with_fake_network(client, monkeypatch):
    vendor = outreach.load_vendor()
    for name in ("get", "_algolia", "resolves"):
        monkeypatch.setattr(vendor, name, getattr(vendor, name))      # ensure they are restored after the test
    _fake_network(vendor)

    r = client.get("/api/yc?action=batches")
    assert r.status_code == 200 and r.headers["cache-control"] == "private, max-age=86400"
    assert [b["batch"] for b in r.json()][:2] == ["Summer 2025", "Winter 2024"]           # newest first

    r = client.get("/api/yc?action=companies&batch=Winter%202024")
    assert r.status_code == 200 and r.json()[0]["slug"] == "acme" and r.json()[0]["one_liner"] == "Rockets."

    r = client.get("/api/yc?action=founders&slugs=acme")
    (company,) = r.json()
    jane, jose = company["founders"]
    assert company["domain"] == "acme.example" and company["site_emails"] == ["jane@acme.example", "sales@acme.example"]   # other.com filtered out
    assert jane["emails_found"] == ["jane@acme.example"] and jane["email_guesses"][0] == "jane@acme.example"
    assert jose["email_guesses"][0] == "jose@acme.example" and "jose.alvarez@acme.example" in jose["email_guesses"]        # accents folded


@pytest.mark.parametrize("qs,msg", [
    ("", "Unknown action"), ("action=evil", "Unknown action"), ("action=companies&batch=Winter%202024%3Bdrop", "Invalid batch"),
    ("action=companies", "Invalid batch"), ("action=founders", "valid slugs"), ("action=founders&slugs=../../etc/passwd", "valid slugs"),
    ("action=founders&slugs=http://127.0.0.1/", "valid slugs"), ("action=founders&slugs=" + ",".join(f"s{i}" for i in range(11)), "valid slugs"),
])
def test_api_yc_rejects_bad_input_and_never_takes_urls(client, qs, msg):
    r = client.get("/api/yc?" + qs)
    assert r.status_code == 400 and msg in r.json()["error"] and r.headers["cache-control"] == "no-store"


def test_api_yc_reports_upstream_failure_as_502(client, monkeypatch):
    vendor = outreach.load_vendor()
    monkeypatch.setattr(vendor, "get", lambda *a, **k: None)
    monkeypatch.setattr(vendor, "_algolia", None)
    r = client.get("/api/yc?action=batches")
    assert r.status_code == 502 and "YC" in r.json()["error"]


# ---------------------------------------------------------------------- the plain-language overlay
OVERLAY_HOOKS = [        # everything app/static/outreach.js looks up in the vendored page
    'id="vars"', 'id="reset"', 'id="list"', 'id="shown"', 'id="all"', 'id="st"', 'id="batch"', 'id="load"', 'id="more"',
    'data-k="my_name"', 'data-k="portfolio"', 'data-k="github"', 'data-k="resume"', 'data-k="subject"', 'data-k="body"',
    '<p class="sub">', "1. Your details", "2. Email template", "4. Find verified emails",
    "function fill(", "function best(", "function contacts(", "let tpl =", "const DEFAULTS", "let ALL = [], DATA = []",
    'data-a="body"', 'data-a="to"', 'data-a="subject"', 'data-a="sent"', 'class="btn mail"', 'class="one"',
    'e.target.textContent === "Expand all"',
]


def test_the_vendored_page_still_has_every_hook_the_overlay_depends_on():
    """Fails right after `update_outreach.sh --apply` if upstream renamed something, before users ever notice."""
    html = (VENDOR / "index.html").read_text()
    missing = [h for h in OVERLAY_HOOKS if h not in html]
    assert not missing, f"upstream page changed; update app/static/outreach.js: {missing}"


def test_overlay_is_injected_after_the_pages_own_script_and_served_under_the_csp(client):
    page = client.get("/outreach").text
    assert len(re.findall(r'<script src="/static/outreach\.js\?v=[0-9a-f]{10}" defer></script>', page)) == 1
    assert page.index("/static/outreach.js") > page.index("yc-outreach-v2")            # after upstream's inline script
    assert client.get("/static/outreach.js").status_code == 200
    assert "script-src 'self'" in client.get("/outreach").headers["content-security-policy"]


def test_the_overlay_is_safe_and_leaves_upstreams_expand_all_label_alone():
    js = (ROOT / "app" / "static" / "outreach.js").read_text()
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)                                  # block comments
    code = "\n".join(re.sub(r"\s//\s.*$", "", l) for l in js.splitlines())         # whole-line and trailing // comments
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "new Function", "document.write"):
        assert banned not in code, f"overlay must not use {banned}"
    assert '$("#all").title' in code                                                # a tooltip is all we add to Expand all...
    assert not re.search(r"#all[^\n]*(textContent|innerText|nodeValue)", code)        # ...its text (which upstream compares against) is untouched


def test_the_overlay_never_creates_details_elements():
    """Upstream's filter()/update() iterate every <details> and expect company data on it; a stray one crashes the page."""
    js = (ROOT / "app" / "static" / "outreach.js").read_text()
    assert not re.search(r'el\(\s*"details"|createElement\(\s*"details"', js)
    assert re.search(r'\$\$\("details", list\)', js)                               # the one place we touch details: scoped to #list
