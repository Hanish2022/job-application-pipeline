"""Integration of the vendored, third-party `yc-outreach` project (MIT, see vendor/yc-outreach/UPSTREAM.json).

We do NOT edit that code. This module:
  * loads vendor/yc-outreach/api/yc.py and serves its JSON API at /api/yc (same path its own UI calls),
  * serves its unmodified UI at /outreach with our navbar and dark theme injected,
  * replaces the vendored module's network function with a guarded one (see below).

Why the guard: yc.py fetches each company's website, and that URL comes from YC's data, which companies control.
On a public host that is harmless; on a laptop next to this no-login dashboard, a hostile value such as
http://127.0.0.1:<port>/... or http://169.254.169.254/... would make *your* machine send requests to its own services.
So every request (and every redirect hop) must resolve to public addresses only, and the connection is pinned to the
address we validated (no second DNS lookup an attacker could change), for http and https alike.
"""
from __future__ import annotations

import http.client
from html import escape
import ipaddress
import re
import socket
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import Optional

from . import config, shell

VENDOR_DIR = config.ROOT / "vendor" / "yc-outreach"
CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "connect-src 'self' https://api.apify.com; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)  # the vendored page is one file with inline JS/CSS, hence 'unsafe-inline'; the rest limits where data can go
NAV_HTML = shell.nav_html("outreach")


class BlockedAddress(OSError):
    """Raised when a request would reach a non-public address."""


# --------------------------------------------------------------------------- address policy
def is_public_ip(ip: str) -> bool:
    """True only for globally routable unicast addresses (incl. when an IPv4 address is wrapped in IPv6)."""
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if isinstance(a, ipaddress.IPv6Address):
        embedded = []
        if a.ipv4_mapped:
            embedded.append(a.ipv4_mapped)
        if a.sixtofour:
            embedded.append(a.sixtofour)
        if a.teredo:
            embedded += list(a.teredo)
        if a in ipaddress.ip_network("64:ff9b::/96"):                      # NAT64
            embedded.append(ipaddress.IPv4Address(int(a) & 0xFFFFFFFF))
        if any(not is_public_ip(str(e)) for e in embedded):
            return False
    return a.is_global and not a.is_multicast


def _connect_public(host: str, port: int, timeout, source_address=None) -> socket.socket:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise OSError(f"cannot resolve {host}") from e
    for *_, sockaddr in infos:
        if not is_public_ip(sockaddr[0]):                                  # ANY non-public answer blocks the whole host
            raise BlockedAddress(f"{host} resolves to a non-public address")
    family, _, proto, _, sockaddr = infos[0]
    sock = socket.socket(family, socket.SOCK_STREAM, proto)
    try:
        if timeout is not None and timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
            sock.settimeout(timeout)
        if source_address:
            sock.bind(source_address)
        sock.connect(sockaddr)                                             # the validated address itself, not the name
        return sock
    except Exception:
        sock.close()
        raise


class _GuardedHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = _connect_public(self.host, self.port, self.timeout, self.source_address)


class _GuardedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        sock = _connect_public(self.host, self.port, self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)   # certificate checked against the NAME


class _HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_GuardedHTTPConnection, req)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_GuardedHTTPSConnection, req, context=self._context)


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not re.match(r"^https?://", newurl, re.I):
            raise urllib.error.HTTPError(newurl, code, "redirect to a non-http(s) URL refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)   # the next hop is guarded like the first


def build_opener() -> urllib.request.OpenerDirector:
    """Only http(s): no file:, ftp:, data: handlers, no environment proxies."""
    opener = urllib.request.OpenerDirector()
    for h in (_HTTPHandler(), _HTTPSHandler(context=ssl.create_default_context()), _Redirects(),
              urllib.request.HTTPDefaultErrorHandler(), urllib.request.HTTPErrorProcessor(), urllib.request.UnknownHandler()):
        opener.add_handler(h)
    return opener


_OPENER = build_opener()


def guarded_get(url: str, timeout=8, data=None, headers=None, ua: str = "Mozilla/5.0") -> Optional[str]:
    """Drop-in replacement for yc.get(): same signature and return contract (text, or None on any failure)."""
    req = urllib.request.Request(url, data=data, headers={"User-Agent": ua, **(headers or {})})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.read(2_000_000).decode("utf-8", "ignore")
    except Exception:
        return None


# ---------------------------------------------------------------------------- vendored module
_vendor: Optional[ModuleType] = None


def load_vendor() -> ModuleType:
    """Import the untouched upstream module once and swap in the guarded network function.

    Compiled in memory (no importlib file loader), so no __pycache__ is ever written into vendor/."""
    global _vendor
    if _vendor is None:
        path = VENDOR_DIR / "api" / "yc.py"
        if not path.exists():
            raise RuntimeError("vendor/yc-outreach is missing - run scripts/update_outreach.sh --apply")
        mod = ModuleType("vendor_yc_outreach")
        mod.__file__ = str(path)
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), mod.__dict__)   # noqa: S102 - reviewed, hash-pinned code
        ua = getattr(mod, "UA", "Mozilla/5.0")
        mod.get = lambda url, timeout=8, data=None, headers=None: guarded_get(url, timeout, data, headers, ua)
        _vendor = mod
    return _vendor


def run_route(query: str) -> tuple[int, object, int]:
    """(status, json-able body, cache seconds) exactly as the upstream handler would answer."""
    try:
        return load_vendor().route(query)
    except Exception as e:  # noqa: BLE001 - same contract as upstream: 502 with a message
        return 502, {"error": str(e) or "Upstream error"}, 0


# -------------------------------------------------------------------------------- the page
def render_page(vendor_dir: Optional[Path] = None) -> str:
    """The upstream index.html, byte-for-byte, plus: our stylesheets, our navbar and a title. Fails loudly if its shape changes."""
    html = ((vendor_dir or VENDOR_DIR) / "index.html").read_text(encoding="utf-8")
    for marker in ("</head>", "<body>", "</body>", "<title>", "</title>"):
        if html.count(marker) != 1:
            raise RuntimeError(f"vendored index.html changed shape ({marker!r}); review it before updating the integration")
    html = re.sub(r"<title>.*?</title>", lambda _m: f"<title>Cold email — {escape(config.APP_NAME)}</title>", html, count=1, flags=re.S)
    html = html.replace("</head>", '<meta name="color-scheme" content="dark">\n<link rel="stylesheet" href="/static/shell.css">\n'
                                   '<link rel="stylesheet" href="/static/outreach.css"></head>', 1)
    html = html.replace("<body>", "<body>" + NAV_HTML, 1)
    # Loaded after the page's own inline script (defer), so it can reuse that script's globals.
    return html.replace("</body>", '<script src="/static/outreach.js" defer></script></body>', 1)
