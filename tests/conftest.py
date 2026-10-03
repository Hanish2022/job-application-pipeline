"""Shared fixtures. Every test gets its own throw-away SQLite file - the real data/jobs.db is never touched."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import Job  # noqa: E402
from app.resume import build_profile  # noqa: E402
from app.store import Store  # noqa: E402

RESUME_TEXT = """Jane Doe
Full Stack Developer | React • Node.js • MongoDB
Technical Skills
Languages: JavaScript, Java, Python, SQL
Frontend: React.js, HTML5, CSS3, Tailwind CSS
Backend: Node.js, Express.js, REST APIs
Databases: MongoDB, MySQL
Projects
Built a React and Node.js app with MongoDB and JWT authentication. React frontend, Node backend.
Express REST APIs, React hooks, Node middleware, MongoDB schemas.
Education
Bachelor of Engineering - Computer Science 2023 - 2027
"""


def pytest_collection_modifyitems(items):
    """Run browser tests last: Playwright's sync API leaves an event loop running in the main
    thread, which would break the plain asyncio.run() used by the (non-browser) tests."""
    items.sort(key=lambda it: "e2e" in Path(str(it.fspath)).parts)


@pytest.fixture(autouse=True)
def _never_use_real_api_keys(monkeypatch):
    """.env is loaded at import, so a dev machine has a real TinyFish key in the environment.
    Tests must never spend it: swap in a dummy (network is mocked by respx anyway)."""
    from app import config
    monkeypatch.setattr(config, "TINYFISH_API_KEY", "test-key")
    monkeypatch.setattr(config, "ADZUNA_APP_ID", "")
    monkeypatch.setattr(config, "ADZUNA_APP_KEY", "")


@pytest.fixture()
def store(tmp_path) -> Store:
    return Store(tmp_path / "test.db")


@pytest.fixture()
def profile() -> dict:
    return build_profile(RESUME_TEXT, "resume.txt")


def make_job(**kw) -> Job:
    base = dict(
        source="greenhouse", external_id="1", title="Software Engineer", company="Acme",
        url="https://example.com/jobs/1", location="Bengaluru, India", description="React and Node.js",
        posted_at="2026-10-01T00:00:00+00:00", board="acme",
    )
    base.update(kw)
    return Job(**base)


@pytest.fixture()
def job():
    return make_job
