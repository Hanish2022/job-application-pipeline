"""Central configuration. Paths can be overridden with environment variables
(the test-suite uses this to point at throw-away databases)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("JOBS_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("JOBS_DB_PATH", DATA_DIR / "jobs.db"))
COMPANIES_PATH = Path(os.environ.get("JOBS_COMPANIES_PATH", ROOT / "companies.json"))
STATIC_DIR = ROOT / "app" / "static"
DEFAULT_RESUME = Path(os.environ.get("JOBS_RESUME", Path.home() / "Downloads" / "new_resume.pdf"))

USER_AGENT = "jobs-pipeline/1.0 (personal job discovery; public job-board APIs only)"
HTTP_TIMEOUT = 30.0
MAX_CONCURRENCY = 8
MAX_DESCRIPTION_CHARS = 8000

# Optional keys (only needed if you enable the aggregator). Read from env / .env
ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY", "")
TINYFISH_API_KEY = os.environ.get("TINYFISH_API_KEY", "")
FEED_REPO_URL = os.environ.get("FEED_REPO_URL", "")   # private git repo holding the daily GitHub-Actions feed


def load_dotenv(path: Path | None = None) -> None:
    """Tiny .env loader (no dependency). Existing environment wins."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()
TINYFISH_API_KEY = os.environ.get("TINYFISH_API_KEY", "")
ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID", ADZUNA_APP_ID)
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY", ADZUNA_APP_KEY)
FEED_REPO_URL = os.environ.get("FEED_REPO_URL", FEED_REPO_URL)
