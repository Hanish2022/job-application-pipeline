"""Asset versioning + no-stale-cache static serving.

Problem this solves: static files used to be sent with only a Last-Modified header. Browsers may then reuse their copy for a
while WITHOUT asking the server, so a fresh HTML page could be paired with a stale stylesheet (unstyled, "scattered" UI that
doesn't match the code). Two fixes:

  * every /static/*.css|js URL in the HTML gets ?v=<content hash>, so a changed file is a different URL and can never be stale;
  * static responses carry Cache-Control: no-cache (always revalidate; the ETag makes that a cheap 304).

finalize() also stamps a short UI build id (hash of all assets) into the logo tooltip so you can see which version is loaded.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from starlette.staticfiles import StaticFiles

from . import config

_cache: dict[str, tuple[tuple[int, int], str]] = {}
_ASSET_RE = re.compile(r'(["\'])(/static/([A-Za-z0-9_.-]+\.(?:css|js)))\1')
BUILD_PLACEHOLDER = "__UI_BUILD__"


def version(name: str, static_dir: Path | None = None) -> str:
    """Short content hash of one asset ("0" if it doesn't exist)."""
    path = (static_dir or config.STATIC_DIR) / name
    try:
        st = path.stat()
    except OSError:
        return "0"
    key = (st.st_mtime_ns, st.st_size)
    hit = _cache.get(str(path))
    if hit and hit[0] == key:
        return hit[1]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:10]
    _cache[str(path)] = (key, digest)
    return digest


def build_id(static_dir: Path | None = None) -> str:
    root = static_dir or config.STATIC_DIR
    names = sorted(p.name for p in root.iterdir() if p.suffix in (".css", ".js"))
    return hashlib.sha256("".join(version(n, root) for n in names).encode()).hexdigest()[:8]


def finalize(html: str, static_dir: Path | None = None) -> str:
    """Version every local css/js URL and fill in the UI build id."""
    html = _ASSET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}?v={version(m.group(3), static_dir)}{m.group(1)}", html)
    return html.replace(BUILD_PLACEHOLDER, build_id(static_dir))


class NoCacheStaticFiles(StaticFiles):
    """Static files that browsers must revalidate every time (ETag/Last-Modified still give cheap 304s)."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response
