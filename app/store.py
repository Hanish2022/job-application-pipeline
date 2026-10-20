"""SQLite persistence: jobs (deduplicated), profile, crawl runs."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

from . import agencies, config
from . import salary as sal
from .matcher import experience_conflict
from .models import Job
from .skills import GENERIC, count_skills
from .textutil import is_india, norm, remote_eligible

VALID_STATUSES = ("new", "saved", "applied", "dismissed")

# Why a job was dismissed (optional; one click on the toast that follows a dismissal).
DISMISS_REASONS = ("wrong_stack", "too_senior", "location", "salary", "agency", "company", "other")
FLAG_KINDS = ("blocked", "agency")        # blocked: never show this company;  agency: hide while "hide staffing agencies" is on
MAX_AGE_DAYS = 60         # a posting older than this counts as "old"
UNSEEN_DAYS = 7           # not seen in a crawl for this long, compared with the NEWEST sighting in the database = "possibly closed"
AVOID_MIN_JOBS = 2        # a technology is "passed on" once it appears in this many jobs you dismissed as "wrong stack"

# Salary filter buckets (LPA). Ticking several means OR. A job matches a range bucket when its advertised range OVERLAPS it
# (so ₹12-18 LPA appears under both "10-15" and "15-25"); "none" matches jobs with no salary.
SALARY_BUCKETS = [("0-5", "Under ₹5 LPA"), ("5-10", "₹5–10 LPA"), ("10-15", "₹10–15 LPA"), ("15-25", "₹15–25 LPA"),
                  ("25-40", "₹25–40 LPA"), ("40-", "₹40 LPA and above"), ("none", "Not listed")]
_SALARY_TOKEN = re.compile(r"^(\d{1,4})-(\d{0,4})$")
MAX_SALARY_TOKENS = 12


def parse_salary_tokens(tokens: Iterable[str]) -> list[str]:
    """Validate + de-duplicate salary bucket tokens: 'none', 'LO-HI' or open-ended 'LO-'. Raises ValueError."""
    out: list[str] = []
    for tok in tokens:
        tok = (tok or "").strip()
        if not tok:
            continue
        if tok != "none":
            m = _SALARY_TOKEN.match(tok)
            if not m or (m.group(2) and int(m.group(1)) >= int(m.group(2))):
                raise ValueError(f"invalid salary range: {tok!r}")
        if tok not in out:
            out.append(tok)
    if len(out) > MAX_SALARY_TOKENS:
        raise ValueError("too many salary ranges")
    return out


def salary_clause(tokens: list[str]) -> tuple[str, list]:
    """SQL for 'matches ANY of these buckets' (tokens must already be validated)."""
    parts, args = [], []
    for tok in tokens:
        if tok == "none":
            parts.append("salary_max_lpa IS NULL")
            continue
        lo, hi = _SALARY_TOKEN.match(tok).groups()
        if hi:
            parts.append("(salary_max_lpa > ? AND salary_min_lpa < ?)")      # strict on both sides: 5-10 does not spill into 10-15
            args += [float(lo), float(hi)]
        else:
            parts.append("salary_max_lpa >= ?")
            args.append(float(lo))
    return "(" + " OR ".join(parts) + ")", args

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
CREATE TABLE IF NOT EXISTS company_flags (
    name_norm TEXT NOT NULL,
    kind      TEXT NOT NULL,
    name      TEXT NOT NULL,
    at        TEXT NOT NULL,
    PRIMARY KEY (name_norm, kind)
);
CREATE TABLE IF NOT EXISTS rejected (
    url TEXT PRIMARY KEY,
    reason TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL
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


def _shift(iso: str, days: int) -> str:
    """An ISO timestamp moved by N days (string-comparable with the timestamps we store)."""
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        t = datetime.now(timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (t + timedelta(days=days)).isoformat(timespec="seconds")


def _name_key(name: str) -> str:
    return (name or "").strip().lower()


_FLAGGED_AGENCY = "(SELECT name_norm FROM company_flags WHERE kind='agency')"
_BLOCKED_SQL = "lower(trim(company)) NOT IN (SELECT name_norm FROM company_flags WHERE kind='blocked')"


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
        if "agency_reason" not in cols:            # staffing-agency detection (app/agencies.py); '' = not an agency
            c.execute("ALTER TABLE jobs ADD COLUMN agency_reason TEXT NOT NULL DEFAULT ''")
            for r in c.execute("SELECT id, company, description FROM jobs").fetchall():
                reason = agencies.agency_reason(r["company"], r["description"])
                if reason:
                    c.execute("UPDATE jobs SET agency_reason=? WHERE id=?", (reason, r["id"]))
        if "dismiss_reason" not in cols:           # why the user dismissed it ('' = no reason given)
            c.execute("ALTER TABLE jobs ADD COLUMN dismiss_reason TEXT NOT NULL DEFAULT ''")

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
                structured = sal.from_structured(job.salary_min, job.salary_max, job.salary_currency, job.salary_period, job.salary)
                if job.salary_lpa_min is not None and job.salary_lpa_max is not None:
                    structured = sal.Salary(job.salary_lpa_min, job.salary_lpa_max, job.salary_currency or "INR", job.salary)
                found = sal.extract(job.salary, job.description, structured=structured)
                lo, hi, cur = (found.min_lpa, found.max_lpa, found.currency) if found else (None, None, "")
                salary_text = job.salary or (found.raw if found else "")
                agency = agencies.agency_reason(job.company, job.description)
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
                               closed=0, last_seen=?, agency_reason=? WHERE id=?""",
                        (job.url, job.description, job.posted_at, salary_text, lo, hi, cur, job.department,
                         job.employment_type, json.dumps(job.tags), row[7], row[8], now, agency, existing["id"]),
                    )
                    updated += 1
                else:
                    c.execute(
                        """INSERT INTO jobs (fingerprint, source, external_id, board, title, company, location,
                               remote, is_india, url, description, posted_at, salary, department,
                               employment_type, tags, first_seen, last_seen,
                               salary_min_lpa, salary_max_lpa, salary_currency, agency_reason)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (*row, lo, hi, cur, agency),
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

    def set_meta(self, key: str, value: str) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def get_meta(self, key: str) -> Optional[str]:
        with self.conn() as c:
            row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def close_fingerprints(self, fingerprints: Iterable[str]) -> int:
        fps = list(fingerprints)
        n = 0
        with self.conn() as c:
            for i in range(0, len(fps), 500):
                chunk = fps[i : i + 500]
                n += c.execute(
                    f"UPDATE jobs SET closed=1 WHERE closed=0 AND fingerprint IN ({','.join('?' * len(chunk))})", chunk
                ).rowcount
        return n

    def rejected_urls(self, days: int = 14) -> set[str]:
        """URLs we fetched and decided against recently (stale, US-only, wrong place…): don't pay to fetch them again."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        with self.conn() as c:
            c.execute("DELETE FROM rejected WHERE at < ?", (cutoff,))
            return {r["url"] for r in c.execute("SELECT url FROM rejected")}

    def add_rejected(self, urls: Iterable[str], reason: str = "filtered") -> int:
        now = now_iso()
        urls = list(urls)
        with self.conn() as c:
            c.executemany(
                "INSERT INTO rejected(url, reason, at) VALUES(?,?,?) ON CONFLICT(url) DO UPDATE SET at=excluded.at",
                [(u, reason, now) for u in urls],
            )
        return len(urls)

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

    def undated(self, source: str, limit: int = 300) -> list[tuple[int, str]]:
        """(id, url) of open jobs from `source` that have no posting date (newest first)."""
        with self.conn() as c:
            rows = c.execute("SELECT id, url FROM jobs WHERE source=? AND closed=0 AND (posted_at IS NULL OR posted_at='') ORDER BY id DESC LIMIT ?",
                             (source, int(limit))).fetchall()
        return [(r["id"], r["url"]) for r in rows]

    def set_posted_at(self, url: str, iso: str) -> bool:
        """Record a posting date for a job that had none (never overwrites a date we already have)."""
        with self.conn() as c:
            return c.execute("UPDATE jobs SET posted_at=? WHERE url=? AND (posted_at IS NULL OR posted_at='')", (iso, url)).rowcount > 0

    def close_url(self, url: str) -> bool:
        with self.conn() as c:
            return c.execute("UPDATE jobs SET closed=1 WHERE url=? AND closed=0", (url,)).rowcount > 0

    def close_unseen(self, sources: list[str], older_than_days: int) -> int:
        """Close search-discovered jobs not seen for N days (they vanished from results => likely filled)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat(timespec="seconds")
        marks = ",".join("?" * len(sources))
        with self.conn() as c:
            return c.execute(
                f"UPDATE jobs SET closed=1 WHERE closed=0 AND source IN ({marks}) AND last_seen < ?", [*sources, cutoff]
            ).rowcount

    def set_status(self, job_id: int, status: str, reason: str = "") -> bool:
        """Change a job's status. A dismissal may carry a reason; 'company' and 'agency' also flag the whole company.
        Moving a job back out of 'dismissed' undoes the flag its dismissal created (so Restore really restores)."""
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {status}")
        if reason and (status != "dismissed" or reason not in DISMISS_REASONS):
            raise ValueError(f"invalid dismiss reason: {reason!r}")
        with self.conn() as c:
            row = c.execute("SELECT company, status, dismiss_reason FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return False
            if row["status"] == "dismissed" and status != "dismissed" and row["dismiss_reason"] in ("company", "agency"):
                kind = "blocked" if row["dismiss_reason"] == "company" else "agency"
                c.execute("DELETE FROM company_flags WHERE name_norm=? AND kind=?", (_name_key(row["company"]), kind))
            elif row["status"] == "dismissed" and status == "dismissed" and row["dismiss_reason"] in ("company", "agency") and reason != row["dismiss_reason"]:
                kind = "blocked" if row["dismiss_reason"] == "company" else "agency"       # the reason was changed: drop the old flag
                c.execute("DELETE FROM company_flags WHERE name_norm=? AND kind=?", (_name_key(row["company"]), kind))
            c.execute("UPDATE jobs SET status=?, dismiss_reason=? WHERE id=?", (status, reason if status == "dismissed" else "", job_id))
            if status == "dismissed" and reason in ("company", "agency"):
                self._flag(c, row["company"], "blocked" if reason == "company" else "agency")
        return True

    # ------------------------------------------------------------ company flags
    @staticmethod
    def _flag(c: sqlite3.Connection, name: str, kind: str) -> None:
        key = _name_key(name)
        if key:
            c.execute("INSERT INTO company_flags(name_norm, kind, name, at) VALUES(?,?,?,?) ON CONFLICT(name_norm, kind) DO NOTHING",
                      (key, kind, name.strip()[:120], now_iso()))

    def flag_company(self, name: str, kind: str) -> bool:
        if kind not in FLAG_KINDS:
            raise ValueError(f"invalid flag kind: {kind!r}")
        if not _name_key(name):
            raise ValueError("empty company name")
        with self.conn() as c:
            self._flag(c, name, kind)
        return True

    def unflag_company(self, name: str, kind: str) -> bool:
        if kind not in FLAG_KINDS:
            raise ValueError(f"invalid flag kind: {kind!r}")
        with self.conn() as c:
            return c.execute("DELETE FROM company_flags WHERE name_norm=? AND kind=?", (_name_key(name), kind)).rowcount > 0

    def company_flags(self) -> dict:
        with self.conn() as c:
            rows = c.execute("SELECT kind, name FROM company_flags ORDER BY lower(name)").fetchall()
        return {"blocked": [r["name"] for r in rows if r["kind"] == "blocked"], "agencies": [r["name"] for r in rows if r["kind"] == "agency"]}

    def learned_avoid_skills(self, min_jobs: int = AVOID_MIN_JOBS) -> list[str]:
        """Technologies that show up in at least `min_jobs` jobs you dismissed as 'wrong stack' (generic ones like git/html ignored).
        Derived on demand from your dismissals, so un-dismissing a job takes its influence away again."""
        seen: dict[str, int] = {}
        with self.conn() as c:
            rows = c.execute("SELECT title, description, tags, department FROM jobs WHERE status='dismissed' AND dismiss_reason='wrong_stack'").fetchall()
        for r in rows:
            for skill in count_skills(f"{r['title']}\n{r['tags']}\n{r['department']}\n{r['description']}"):
                if skill not in GENERIC:
                    seen[skill] = seen.get(skill, 0) + 1
        return sorted(k for k, n in seen.items() if n >= min_jobs)

    def jobs_for_insight(self, min_score: int = 50, limit: int = 250) -> list[sqlite3.Row]:
        """The best-matching jobs still on your plate (new + saved, not hidden), with text, for the skills-gap analysis."""
        with self.conn() as c:
            where, args = self._hiding(c, True, True)
            return c.execute(
                f"SELECT title, description, tags, department, score FROM jobs WHERE closed=0 AND status IN ('new','saved') AND score >= ? "
                f"AND {_BLOCKED_SQL}{where} ORDER BY score DESC, id DESC LIMIT ?", [int(min_score), *args, int(limit)]).fetchall()

    # ------------------------------------------------------------- hide rules
    @staticmethod
    def _hiding(c: sqlite3.Connection, hide_old: bool, hide_agencies: bool) -> tuple[str, list]:
        """SQL (starting with ' AND ') for the two "tidy up" rules. They only apply to the Active list, and never hide a job you saved."""
        rules = Store._rules(c, hide_old, hide_agencies)
        return "".join(f" AND {sql}" for sql, _ in rules.values()), [a for _, args in rules.values() for a in args]

    @staticmethod
    def _cutoffs(c: sqlite3.Connection) -> tuple[str, str]:
        """(posted before this = old, last seen before this = possibly closed).  'Seen' is measured against the NEWEST sighting in
        the database, not the clock, so a few weeks without a crawl doesn't make every job look closed."""
        newest = c.execute("SELECT MAX(last_seen) FROM jobs").fetchone()[0]
        return _shift(now_iso(), -MAX_AGE_DAYS), (_shift(newest, -UNSEEN_DAYS) if newest else now_iso())

    @staticmethod
    def _rules(c: sqlite3.Connection, hide_old: bool, hide_agencies: bool) -> dict[str, tuple[str, list]]:
        rules: dict[str, tuple[str, list]] = {}
        if hide_old:
            age_cutoff, seen_cutoff = Store._cutoffs(c)
            rules["old"] = ("(status='saved' OR (COALESCE(NULLIF(posted_at,''), first_seen) >= ? AND last_seen >= ?))", [age_cutoff, seen_cutoff])
        if hide_agencies:
            rules["agencies"] = (f"(status='saved' OR (agency_reason = '' AND lower(trim(company)) NOT IN {_FLAGGED_AGENCY}))", [])
        return rules

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
            flagged, cutoffs = self._flagged_agencies(c), self._cutoffs(c)
        return _row_to_dict(row, flagged_agencies=flagged, cutoffs=cutoffs) if row else None

    @staticmethod
    def _flagged_agencies(c: sqlite3.Connection) -> set[str]:
        return {r[0] for r in c.execute("SELECT name_norm FROM company_flags WHERE kind='agency'")}

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
        salary: Optional[list[str]] = None,     # bucket tokens, OR-ed (see SALARY_BUCKETS)
        sort: str = "score",
        limit: int = 30,
        offset: int = 0,
        include_closed: bool = False,
        hide_old: bool = False,        # Active list only: drop postings older than MAX_AGE_DAYS and ones not seen in a crawl lately
        hide_agencies: bool = False,   # Active list only: drop staffing agencies / body-shops
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
        if salary:
            clause, clause_args = salary_clause(parse_salary_tokens(salary))
            where.append(clause)
            args += clause_args
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
            where.append("status NOT IN ('dismissed', 'applied')")      # "Active" = still to act on: new + saved
            where.append(_BLOCKED_SQL)                                  # companies you chose never to see
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
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        active_list = not statuses
        with self.conn() as c:
            rules = self._rules(c, hide_old and active_list, hide_agencies and active_list)
            # Count what each rule hides (given every OTHER filter) so the page can say "12 older postings are hidden".
            base = (" WHERE " + " AND ".join(where)) if where else ""
            hidden = {name: c.execute(f"SELECT COUNT(*) FROM jobs{base}{' AND' if base else ' WHERE'} NOT {sql}", [*args, *rargs]).fetchone()[0]
                      for name, (sql, rargs) in rules.items()}
            for sql, rargs in rules.values():
                where.append(sql)
                args += rargs
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            total = c.execute(f"SELECT COUNT(*) FROM jobs{clause}", args).fetchone()[0]
            rows = c.execute(
                f"SELECT * FROM jobs{clause} ORDER BY {order} LIMIT ? OFFSET ?", [*args, limit, offset]
            ).fetchall()
            flagged, cutoffs = self._flagged_agencies(c), self._cutoffs(c)
        return {"total": total, "items": [_row_to_dict(r, brief=True, flagged_agencies=flagged, cutoffs=cutoffs) for r in rows], "limit": limit, "offset": offset,
                "hidden": {"old": hidden.get("old", 0), "agencies": hidden.get("agencies", 0)}}

    def stats(self, hide_old: bool = False, hide_agencies: bool = False) -> dict:
        """Counts for the dashboard. The 'strong matches' and salary-range counts describe the Active list, so they follow the
        same hide rules the list uses (otherwise a count could promise jobs the list doesn't show)."""
        with self.conn() as c:
            hide_sql, hide_args = self._hiding(c, hide_old, hide_agencies)
            active = f"closed=0 AND status NOT IN ('dismissed', 'applied') AND {_BLOCKED_SQL}{hide_sql}"
            total = c.execute("SELECT COUNT(*) FROM jobs WHERE closed=0").fetchone()[0]
            by_status = {r["status"]: r["n"] for r in c.execute(
                "SELECT status, COUNT(*) n FROM jobs WHERE closed=0 GROUP BY status")}
            by_source = {r["source"]: r["n"] for r in c.execute(
                "SELECT source, COUNT(*) n FROM jobs WHERE closed=0 GROUP BY source")}
            with_salary = c.execute("SELECT COUNT(*) FROM jobs WHERE closed=0 AND salary_max_lpa IS NOT NULL").fetchone()[0]
            strong = c.execute(f"SELECT COUNT(*) FROM jobs WHERE {active} AND score>=60", hide_args).fetchone()[0]
            # "New" = first found in the last 24 h AND not an old posting we only just noticed (a year-old job is not news)
            new_today = c.execute(
                "SELECT COUNT(*) FROM jobs WHERE closed=0 AND first_seen >= ? AND COALESCE(NULLIF(posted_at,''), first_seen) >= ?",
                ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds"), _shift(now_iso(), -MAX_AGE_DAYS)),
            ).fetchone()[0]
            buckets = {}
            for token, _label in SALARY_BUCKETS:        # counts across the jobs you still have to act on (the Active tab)
                clause, clause_args = salary_clause([token])
                buckets[token] = c.execute(f"SELECT COUNT(*) FROM jobs WHERE {active} AND {clause}", [*hide_args, *clause_args]).fetchone()[0]
            last = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return {
            "total": total,
            "strong_matches": strong,
            "with_salary": with_salary,
            "salary_buckets": buckets,
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

    def last_ok_finished_at(self) -> Optional[str]:
        with self.conn() as c:
            row = c.execute("SELECT finished_at FROM runs WHERE status='ok' AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        return row["finished_at"] if row else None

    def recent_runs(self, n: int = 10) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [_run_to_dict(r) for r in rows]


def _run_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["stats"] = json.loads(d.get("stats") or "{}")
    return d


def _row_to_dict(row: sqlite3.Row, brief: bool = False, flagged_agencies: Optional[set] = None,
                 cutoffs: Optional[tuple[str, str]] = None) -> dict:
    d = dict(row)
    conflict = experience_conflict(d.get("description") or "") if d.get("level") == "unknown" else None
    d["exp_conflict"] = {"header": conflict[0], "text": conflict[1]} if conflict else None     # the page contradicts itself about experience
    if cutoffs:
        age_cutoff, seen_cutoff = cutoffs
        d["old"] = (d.get("posted_at") or d["first_seen"]) < age_cutoff            # posted long ago (undated jobs: first seen long ago)
        d["possibly_closed"] = d["last_seen"] < seen_cutoff                       # not seen in a crawl for a week while others were
    d["agency"] = d.pop("agency_reason", "") or ("You marked this company as a staffing agency" if _name_key(d["company"]) in (flagged_agencies or ()) else "")
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
