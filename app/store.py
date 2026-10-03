"""SQLite persistence: jobs (deduplicated), profile, crawl runs."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

from . import config
from . import salary as sal
from .models import Job
from .textutil import is_india, norm, remote_eligible

VALID_STATUSES = ("new", "saved", "applied", "dismissed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint     TEXT NOT NULL UNIQUE,
    source          TEXT NOT NULL,
    external_id     TEXT NOT NULL,
    board           TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL,
    company         TEXT NOT NULL,
    location        TEXT NOT NULL DEFAULT '',
    remote          INTEGER NOT NULL DEFAULT 0,
    is_india        INTEGER NOT NULL DEFAULT 0,
    url             TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    posted_at       TEXT,
    salary          TEXT NOT NULL DEFAULT '',
    salary_min_lpa  REAL,
    salary_max_lpa  REAL,
    salary_currency TEXT NOT NULL DEFAULT '',
    department      TEXT NOT NULL DEFAULT '',
    employment_type TEXT NOT NULL DEFAULT '',
    tags            TEXT NOT NULL DEFAULT '[]',
    level           TEXT NOT NULL DEFAULT 'unknown',
    score           INTEGER NOT NULL DEFAULT 0,
    matched         TEXT NOT NULL DEFAULT '[]',
    reasons         TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'new',
    closed          INTEGER NOT NULL DEFAULT 0,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_board ON jobs(source, board);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL DEFAULT 'running',
    stats       TEXT NOT NULL DEFAULT '{}'
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fingerprint(job: Job) -> str:
    """Cross-source identity: same company + title + location == same job."""
    key = "|".join((norm(job.company), norm(job.title), norm(job.location)))
    return hashlib.sha1(key.encode()).hexdigest()


class Store:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else config.DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)
            self._migrate(c)

    @staticmethod
    def _migrate(c: sqlite3.Connection) -> None:
        """Add salary columns to databases created before they existed, and backfill them."""
        cols = {r["name"] for r in c.execute("PRAGMA table_info(jobs)")}
        if "salary_min_lpa" not in cols:
            c.execute("ALTER TABLE jobs ADD COLUMN salary_min_lpa REAL")
            c.execute("ALTER TABLE jobs ADD COLUMN salary_max_lpa REAL")
            c.execute("ALTER TABLE jobs ADD COLUMN salary_currency TEXT NOT NULL DEFAULT ''")
            for r in c.execute("SELECT id, salary, description FROM jobs").fetchall():
                found = sal.extract(r["salary"], r["description"])
                if found:
                    c.execute(
                        "UPDATE jobs SET salary_min_lpa=?, salary_max_lpa=?, salary_currency=?, "
                        "salary=CASE WHEN salary='' THEN ? ELSE salary END WHERE id=?",
                        (found.min_lpa, found.max_lpa, found.currency, found.raw, r["id"]),
                    )
        c.execute("CREATE INDEX IF NOT EXISTS idx_jobs_salary ON jobs(salary_max_lpa)")

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    # ------------------------------------------------------------------ writes
    def upsert_jobs(self, jobs: Iterable[Job]) -> dict:
        """Insert new jobs / refresh existing ones. User status is never overwritten."""
        inserted = updated = 0
        now = now_iso()
        with self.conn() as c:
            for job in jobs:
                if not job.is_valid():
                    continue
                fp = fingerprint(job)
                loc = job.location or ""
                found = sal.extract(
                    job.salary, job.description,
                    structured=sal.from_structured(job.salary_min, job.salary_max, job.salary_currency, job.salary_period, job.salary),
                )
                lo, hi, cur = (found.min_lpa, found.max_lpa, found.currency) if found else (None, None, "")
                salary_text = job.salary or (found.raw if found else "")
                row = (
                    fp, job.source, str(job.external_id), job.board, job.title.strip(), job.company.strip(),
                    loc, int(remote_eligible(loc, job.remote)), int(is_india(loc)), job.url,
                    job.description, job.posted_at, salary_text, job.department, job.employment_type,
                    json.dumps(job.tags), now, now,
                )
                existing = c.execute("SELECT id, source, salary_max_lpa FROM jobs WHERE fingerprint=?", (fp,)).fetchone()
                if existing and existing["source"] != job.source:
                    # Same job seen on another source: keep the original link/description, just mark it alive
                    # and fill in pay if we didn't have it.
                    c.execute(
                        "UPDATE jobs SET closed=0, last_seen=?, "
                        "salary=CASE WHEN salary_max_lpa IS NULL THEN ? ELSE salary END, "
                        "salary_min_lpa=COALESCE(salary_min_lpa, ?), salary_max_lpa=COALESCE(salary_max_lpa, ?), "
                        "salary_currency=CASE WHEN salary_max_lpa IS NULL THEN ? ELSE salary_currency END WHERE id=?",
                        (now, salary_text, lo, hi, cur, existing["id"]),
                    )
                    updated += 1
                elif existing:
                    c.execute(
                        """UPDATE jobs SET url=?, description=?, posted_at=COALESCE(?, posted_at), salary=?,
                               salary_min_lpa=?, salary_max_lpa=?, salary_currency=?,
                               department=?, employment_type=?, tags=?, remote=?, is_india=?,
                               closed=0, last_seen=? WHERE id=?""",
                        (job.url, job.description, job.posted_at, salary_text, lo, hi, cur, job.department,
                         job.employment_type, json.dumps(job.tags), row[7], row[8], now, existing["id"]),
                    )
                    updated += 1
                else:
                    c.execute(
                        """INSERT INTO jobs (fingerprint, source, external_id, board, title, company, location,
                               remote, is_india, url, description, posted_at, salary, department,
                               employment_type, tags, first_seen, last_seen,
                               salary_min_lpa, salary_max_lpa, salary_currency)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (*row, lo, hi, cur),
                    )
                    inserted += 1
        return {"inserted": inserted, "updated": updated}

    def close_missing(self, source: str, board: str, seen: set[str]) -> int:
        """Mark jobs from a (source, board) that were NOT in the latest successful fetch as closed."""
        with self.conn() as c:
            rows = c.execute(
                "SELECT id, fingerprint FROM jobs WHERE source=? AND board=? AND closed=0", (source, board)
            ).fetchall()
            ids = [r["id"] for r in rows if r["fingerprint"] not in seen]
            for i in range(0, len(ids), 500):
                chunk = ids[i : i + 500]
                c.execute(f"UPDATE jobs SET closed=1 WHERE id IN ({','.join('?' * len(chunk))})", chunk)
            return len(ids)

    def known_urls(self, sources: list[str], preview_tag: str = "via-search") -> dict[str, bool]:
        """{url: has_full_details} for stored jobs of the given sources (preview-only jobs are retried)."""
        marks = ",".join("?" * len(sources))
        with self.conn() as c:
            rows = c.execute(f"SELECT url, tags FROM jobs WHERE source IN ({marks})", sources).fetchall()
        return {r["url"]: preview_tag not in json.loads(r["tags"] or "[]") for r in rows}

    def touch_urls(self, urls: Iterable[str]) -> int:
        """Mark jobs as still visible in a discovery source (refresh last_seen, re-open if closed)."""
        urls = list(urls)
        now = now_iso()
        n = 0
        with self.conn() as c:
            for i in range(0, len(urls), 500):
                chunk = urls[i : i + 500]
                n += c.execute(
                    f"UPDATE jobs SET last_seen=?, closed=0 WHERE url IN ({','.join('?' * len(chunk))})", [now, *chunk]
                ).rowcount
        return n

    def close_unseen(self, sources: list[str], older_than_days: int) -> int:
        """Close search-discovered jobs not seen for N days (they vanished from results => likely filled)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat(timespec="seconds")
        marks = ",".join("?" * len(sources))
        with self.conn() as c:
            return c.execute(
                f"UPDATE jobs SET closed=1 WHERE closed=0 AND source IN ({marks}) AND last_seen < ?", [*sources, cutoff]
            ).rowcount

    def set_status(self, job_id: int, status: str) -> bool:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {status}")
        with self.conn() as c:
            cur = c.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
            return cur.rowcount > 0

    def update_scores(self, rows: Iterable[tuple[int, int, str, list, list]]) -> int:
        n = 0
        with self.conn() as c:
            for job_id, score, level, matched, reasons in rows:
                c.execute(
                    "UPDATE jobs SET score=?, level=?, matched=?, reasons=? WHERE id=?",
                    (score, level, json.dumps(matched), json.dumps(reasons), job_id),
                )
                n += 1
        return n

    # ------------------------------------------------------------------- reads
    def all_for_scoring(self) -> list[sqlite3.Row]:
        with self.conn() as c:
            return c.execute(
                "SELECT id, title, description, location, remote, is_india, department, tags FROM jobs"
            ).fetchall()

    def get_job(self, job_id: int) -> Optional[dict]:
        with self.conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _row_to_dict(row) if row else None

    def query_jobs(
        self,
        q: str = "",
        min_score: int = 0,
        levels: Optional[list[str]] = None,
        locations: Optional[list[str]] = None,   # subset of {"india", "remote"}; empty = anywhere
        sources: Optional[list[str]] = None,
        statuses: Optional[list[str]] = None,
        company: str = "",
        posted_within_days: Optional[int] = None,
        min_lpa: Optional[float] = None,
        has_salary: bool = False,
        sort: str = "score",
        limit: int = 30,
        offset: int = 0,
        include_closed: bool = False,
    ) -> dict:
        where, args = [], []
        if not include_closed:
            where.append("closed=0")
        if q.strip():
            for term in q.lower().split():
                like = f"%{term}%"
                where.append(
                    "(lower(title) LIKE ? OR lower(company) LIKE ? OR lower(location) LIKE ? "
                    "OR lower(department) LIKE ? OR lower(tags) LIKE ? OR lower(description) LIKE ?)"
                )
                args += [like] * 6
        if company.strip():
            where.append("lower(company) LIKE ?")
            args.append(f"%{company.strip().lower()}%")
        if min_score:
            where.append("score >= ?")
            args.append(int(min_score))
        if min_lpa:
            where.append("salary_max_lpa >= ?")          # the top of the advertised range reaches the threshold
            args.append(float(min_lpa))
        elif has_salary:
            where.append("salary_max_lpa IS NOT NULL")
        if levels:
            where.append(f"level IN ({','.join('?' * len(levels))})")
            args += levels
        if sources:
            where.append(f"source IN ({','.join('?' * len(sources))})")
            args += sources
        if locations:
            parts = []
            if "india" in locations:
                parts.append("is_india=1")
            if "remote" in locations:
                parts.append("remote=1")
            if parts:
                where.append("(" + " OR ".join(parts) + ")")
        if statuses:
            where.append(f"status IN ({','.join('?' * len(statuses))})")
            args += statuses
        else:
            where.append("status != 'dismissed'")
        if posted_within_days:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=int(posted_within_days))).isoformat(timespec="seconds")
            # Fall back to first_seen when the source gives no posting date.
            where.append("COALESCE(posted_at, first_seen) >= ?")
            args.append(cutoff)

        order = {
            "score": "score DESC, COALESCE(posted_at, first_seen) DESC, id DESC",
            "newest": "COALESCE(posted_at, first_seen) DESC, score DESC, id DESC",
            "company": "lower(company) ASC, score DESC, id DESC",
            "salary": "salary_max_lpa IS NULL, salary_max_lpa DESC, score DESC, id DESC",
        }.get(sort, "score DESC, id DESC")
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        with self.conn() as c:
            total = c.execute(f"SELECT COUNT(*) FROM jobs{clause}", args).fetchone()[0]
            rows = c.execute(
                f"SELECT * FROM jobs{clause} ORDER BY {order} LIMIT ? OFFSET ?", [*args, limit, offset]
            ).fetchall()
        return {"total": total, "items": [_row_to_dict(r, brief=True) for r in rows], "limit": limit, "offset": offset}

    def stats(self) -> dict:
        with self.conn() as c:
            total = c.execute("SELECT COUNT(*) FROM jobs WHERE closed=0").fetchone()[0]
            by_status = {r["status"]: r["n"] for r in c.execute(
                "SELECT status, COUNT(*) n FROM jobs WHERE closed=0 GROUP BY status")}
            by_source = {r["source"]: r["n"] for r in c.execute(
                "SELECT source, COUNT(*) n FROM jobs WHERE closed=0 GROUP BY source")}
            with_salary = c.execute("SELECT COUNT(*) FROM jobs WHERE closed=0 AND salary_max_lpa IS NOT NULL").fetchone()[0]
            strong = c.execute("SELECT COUNT(*) FROM jobs WHERE closed=0 AND score>=60 AND status!='dismissed'").fetchone()[0]
            new_today = c.execute(
                "SELECT COUNT(*) FROM jobs WHERE closed=0 AND first_seen >= ?",
                ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds"),),
            ).fetchone()[0]
            last = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return {
            "total": total,
            "strong_matches": strong,
            "with_salary": with_salary,
            "new_last_24h": new_today,
            "by_status": by_status,
            "by_source": by_source,
            "last_run": _run_to_dict(last) if last else None,
        }

    # ----------------------------------------------------------------- profile
    def save_profile(self, profile: dict) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO kv(key, value) VALUES('profile', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(profile),),
            )

    def load_profile(self) -> Optional[dict]:
        with self.conn() as c:
            row = c.execute("SELECT value FROM kv WHERE key='profile'").fetchone()
        return json.loads(row["value"]) if row else None

    # -------------------------------------------------------------------- runs
    def start_run(self) -> int:
        with self.conn() as c:
            return c.execute("INSERT INTO runs(started_at) VALUES(?)", (now_iso(),)).lastrowid

    def running_run(self) -> Optional[dict]:
        """A run that started recently and hasn't finished (stale ones >2h are ignored)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
        with self.conn() as c:
            row = c.execute(
                "SELECT * FROM runs WHERE status='running' AND started_at >= ? ORDER BY id DESC LIMIT 1", (cutoff,)
            ).fetchone()
        return _run_to_dict(row) if row else None

    def finish_run(self, run_id: int, status: str, stats: dict) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE runs SET finished_at=?, status=?, stats=? WHERE id=?",
                (now_iso(), status, json.dumps(stats), run_id),
            )

    def recent_runs(self, n: int = 10) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [_run_to_dict(r) for r in rows]


def _run_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["stats"] = json.loads(d.get("stats") or "{}")
    return d


def _row_to_dict(row: sqlite3.Row, brief: bool = False) -> dict:
    d = dict(row)
    for k in ("tags", "matched", "reasons"):
        d[k] = json.loads(d.get(k) or "[]")
    d["remote"] = bool(d["remote"])
    d["is_india"] = bool(d["is_india"])
    d["closed"] = bool(d["closed"])
    lo, hi = d.get("salary_min_lpa"), d.get("salary_max_lpa")
    converted = bool(d.get("salary_currency")) and d["salary_currency"] != "INR"
    d["salary_lpa"] = sal.format_lpa(lo, hi, converted) if lo is not None else ""
    d["salary_converted"] = converted if lo is not None else False
    if brief:
        desc = d.pop("description", "") or ""
        d["snippet"] = desc[:280].replace("\n", " ").strip()
    d.pop("fingerprint", None)
    return d
