"""Wellfound + YC "Work at a Startup" discovery through TinyFish (https://docs.tinyfish.ai).

Neither site offers a public jobs API, and we deliberately do NOT log in to or scrape them ourselves.
Instead:
  1. TinyFish *Search* returns public posting URLs for queries like  site:wellfound.com/jobs "full stack" India
  2. For new, relevant-looking results, TinyFish *Fetch* renders the posting page and returns markdown,
     from which we read location, remote policy, pay, experience, posted date and the description.

Trade-offs (be aware):
  * Search results can be cached/stale -> every fetched page is checked for closed-job markers and for age.
  * A fetch error ("bot_blocked", timeout…) does NOT prove a job is dead, so such results are kept as
    search-only *previews* (tagged "via-search") and retried on the next run.
  * Calls are capped per run (max_fetch) to stay inside the free tier.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Optional
from urllib.parse import urlparse

from .. import config
from ..models import Job
from ..textutil import html_to_text, to_iso
from .http import get_json, post_json

SEARCH_URL = "https://api.search.tinyfish.ai"
FETCH_URL = "https://api.fetch.tinyfish.ai"
PREVIEW_TAG = "via-search"

# host -> (source name, posting-URL regex). Only individual postings, never listing/company pages.
SITES: dict[str, tuple[str, re.Pattern]] = {
    "wellfound.com": ("wellfound", re.compile(r"^https?://(?:www\.)?wellfound\.com/jobs/(\d+)", re.I)),
    "workatastartup.com": ("yc", re.compile(r"^https?://(?:www\.)?workatastartup\.com/jobs/(\d+)(?:$|[/?#])", re.I)),
}

_DEAD = re.compile(
    r"no longer (?:accepting|available|open|hiring)|position (?:has been|is) (?:filled|closed)|"
    r"(?:job|posting|listing|role) (?:has been |is |was )?(?:closed|expired|removed|filled|unavailable)|"
    r"this job (?:is )?(?:no longer|has expired)|job not found|page not found|applications? (?:are )?closed",
    re.I,
)
_US_ONLY = re.compile(
    r"\bu\.?s\.?\s*citizen(?:s|ship)?(?:\s*/\s*visa)?\s*(?:holders?\s*)?only\b|\bus[-\s]only\b|"
    r"\bmust (?:be|reside|live|work)\s+(?:located\s+|based\s+|residing\s+)?(?:in|within)\s+(?:the\s+)?(?:u\.?s\.?a?\b|united states)|"
    r"\bauthori[sz]ed to work in (?:the )?(?:u\.?s\.?a?\b|united states)|\bgreen\s?card\s+(?:holder|required)|"
    r"\brequires? (?:u\.?s\.?|us) work",
    re.I,
)
_PAY_HEADER = re.compile(
    r"[₹$€£]\s*[\d.,]+\s*(?:lpa|lakhs?|cr|k|m|l)?\s*[–\-—]+\s*[₹$€£]?\s*[\d.,]+\s*(?:lpa|lakhs?|cr|k|m|l)?(?:\s*(?:INR|USD|EUR|GBP))?", re.I)
_JOBTYPE = r"(?:Full[- ]?time|Part[- ]?time|Contract|Internship|Intern\b)"
_YC_COMPACT = re.compile(rf"^(?P<loc>[^#\n]{{2,300}}?)(?P<type>{_JOBTYPE})(?P<rest>[^\n]*)$", re.I | re.M)
_YC_EXP = re.compile(r"(\d+\+?\s*(?:[-–]\s*\d+\s*)?years?|Any\s*\(new grads ok\)|New grads? ok)", re.I)
_REL_TIME = re.compile(r"(\d+)\s*(minute|hour|day|week|month|year)s?\s+ago", re.I)
_UNIT_DAYS = {"minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30, "year": 365}


def enabled() -> bool:
    return bool(config.TINYFISH_API_KEY)


def _headers() -> dict:
    return {"X-API-Key": config.TINYFISH_API_KEY}


# --------------------------------------------------------------------------- search
def identify(url: str) -> Optional[tuple[str, str, str]]:
    """(source, external_id, clean_url) for a supported individual posting URL, else None."""
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    site = SITES.get(host)
    if not site:
        return None
    m = site[1].match(url)
    if not m:
        return None
    clean = url.split("?")[0].split("#")[0]
    return site[0], m.group(1), clean


def parse_search_title(raw: str) -> tuple[str, str, str]:
    """'Software Engineer Intern at Vite • New Delhi' -> (title, company, location)."""
    t = (raw or "").strip()
    t = re.sub(r"\s*\|\s*Y Combinator.*$", "", t, flags=re.I)
    t = re.sub(r"\s*[|\-–]\s*(?:Wellfound|AngelList).*$", "", t, flags=re.I)
    location = ""
    if " • " in t:
        t, _, rest = t.partition(" • ")
        location = ", ".join(p.strip() for p in rest.split(" • ") if p.strip())
    company = ""
    if " at " in t:
        head, _, tail = t.rpartition(" at ")
        if head.strip() and tail.strip():
            t, company = head, tail
    elif " | " in t:
        t = t.split(" | ")[0]
    company = re.sub(r"\s*\([WSFX]\d{2}\)\s*$", "", company).strip(" …")
    return t.strip(" …"), company, location.strip(" …")


def parse_search(data: dict) -> list[Job]:
    """Search results -> preview Jobs (no description/location guarantees)."""
    out: list[Job] = []
    for r in data.get("results", []):
        ident = identify(r.get("url") or "")
        if not ident:
            continue
        source, ext_id, url = ident
        title, company, location = parse_search_title(r.get("title") or "")
        snippet = html_to_text(r.get("snippet") or "", 600)
        if not title:
            continue
        out.append(Job(
            source=source, external_id=ext_id, title=title, company=company or "Unknown", url=url,
            location=location, description=snippet, board=source, tags=[PREVIEW_TAG],
        ))
    return out


async def search(client, query: str, page: int = 0) -> list[Job]:
    data = await get_json(client, SEARCH_URL, params={"query": query, "page": page}, headers=_headers())
    return parse_search(data)


# ---------------------------------------------------------------------------- fetch
def relative_to_iso(text: str, now: Optional[datetime] = None) -> Optional[str]:
    """'2 weeks ago' / 'yesterday' / 'today' -> approximate ISO timestamp."""
    now = now or datetime.now(timezone.utc)
    low = (text or "").lower()
    if "just now" in low or "today" in low:
        return now.isoformat(timespec="seconds")
    if "yesterday" in low:
        return (now - timedelta(days=1)).isoformat(timespec="seconds")
    m = _REL_TIME.search(low)
    if not m:
        return None
    days = int(m.group(1)) * _UNIT_DAYS[m.group(2).lower()]
    return (now - timedelta(days=days)).isoformat(timespec="seconds")


def _after(label: str, text: str) -> str:
    """Value on the line(s) after a label such as 'Job Location' (Wellfound layout)."""
    m = re.search(rf"^\s*{re.escape(label)}\s*\n+\s*([^\n]+)", text, re.I | re.M)
    return m.group(1).strip() if m else ""


def parse_detail(text: str) -> dict:
    """Pull structured fields out of a rendered posting (markdown). Missing fields are simply absent."""
    info: dict = {}
    if not text:
        return info
    head = text[:2500]
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if lines:
        info["company"] = re.sub(r"\s*[·•]\s*[WSFX]\d{2}$", "", lines[0]).strip()[:80]
    loc = _after("Job Location", text)                                   # Wellfound
    if not loc:
        m = re.search(r"^\s*Location:\s*(.+)$", text, re.I | re.M)         # YC, free-text layout
        loc = m.group(1).strip() if m else ""
    if not loc:
        m = re.search(r"^\s*Location(?![: ])\s*(.+)$", head, re.I | re.M)   # YC, "LocationNew Delhi, DL, IN"
        loc = m.group(1).strip() if m else ""
    yc_exp = ""
    if not loc:
        m = _YC_COMPACT.search(head)                                       # YC, "Pune, IndiaFull-time…3+ years"
        if m:
            loc = m.group("loc").strip(" /")
            em = _YC_EXP.search(m.group("rest"))
            yc_exp = em.group(1) if em else ""
    info["location"] = loc[:160]
    policy = _after("Remote Work Policy", text)
    info["remote_policy"] = policy
    m = re.search(r"No experience required|\d+\+?\s*(?:[-–]\s*\d+\s*)?years? of experience", head, re.I)
    if m:
        info["experience"] = m.group(0)
    elif yc_exp:
        info["experience"] = yc_exp
    else:
        m = re.search(r"^\s*Experience\s*(\d+\+?\s*years?)", head, re.I | re.M)
        if m:
            info["experience"] = m.group(1)
    m = re.search(r"(Internship|Full[- ]?time|Part[- ]?time|Contract)", head, re.I)
    if m:
        info["employment_type"] = m.group(1)
    m = re.search(r"Posted:\s*([^•\n]+)", head)
    if m:
        info["posted_at"] = relative_to_iso(m.group(1))
        info["posted_text"] = m.group(1).strip()
    m = _PAY_HEADER.search(head)
    if m:
        info["salary"] = m.group(0).strip()
    else:
        m = re.search(r"Compensation:\s*([^\n]+)", head, re.I)
        if m:
            info["salary"] = m.group(1).strip()[:80]
    skills = re.search(r"\n\s*Skills\s*\n+(.*?)(?:\n\s*##|\Z)", text, re.S)
    if skills:
        info["skills"] = [s.strip() for s in skills.group(1).splitlines() if s.strip()][:12]
    about = re.search(r"##\s*About the job\s*(.*)", text, re.S | re.I)
    body = (about.group(1) if about else text).strip()
    info["description"] = re.sub(r"\\\*+|\*{2,}", "", re.sub(r"\n{3,}", "\n\n", body))[: config.MAX_DESCRIPTION_CHARS]
    dead = _DEAD.search(head)
    info["dead"] = bool(dead)
    return info


def apply_detail(job: Job, info: dict) -> Job:
    """Merge parsed page details into a preview Job (keeps the search data where the page is silent)."""
    if info.get("company") and job.company in ("", "Unknown"):
        job.company = info["company"]
    loc = info.get("location") or ""
    policy = (info.get("remote_policy") or "").lower()
    if policy.startswith("remote") and "remote" not in loc.lower():
        loc = f"Remote – {loc}" if loc else "Remote"
    if loc:
        job.location = loc
    if policy.startswith("remote"):
        job.remote = True
    if info.get("description"):
        exp = info.get("experience")
        if exp:
            exp = exp.rstrip(".")
            exp = exp if re.match(r"(no experience|any|new grad)", exp, re.I) else f"Experience: {exp}"
        job.description = (f"{exp}.\n\n" if exp else "") + info["description"]
    if info.get("posted_at"):
        job.posted_at = info["posted_at"]
    if info.get("salary"):
        job.salary = info["salary"]
    if info.get("employment_type"):
        job.employment_type = info["employment_type"]
    tags = [t for t in job.tags if t != PREVIEW_TAG]
    if re.search(r"no experience required|new grads? ok", info.get("experience", ""), re.I):
        tags.append("Entry-level")
    if re.search(r"intern", info.get("employment_type", ""), re.I):
        tags.append("Intern")
    tags += info.get("skills", [])
    job.tags = tags[:14]
    return job


async def fetch_details(client, urls: list[str]) -> tuple[dict[str, dict], dict[str, str]]:
    """Batch-fetch up to 10 URLs. Returns ({url: parsed_info}, {url: error})."""
    data = await post_json(client, FETCH_URL, {"urls": urls[:10], "format": "markdown"}, headers=_headers())
    ok = {}
    for r in data.get("results", []):
        key = (r.get("url") or "").split("?")[0]
        ok[key] = parse_detail(r.get("text") or "")
    errs = {(e.get("url") or "").split("?")[0]: str(e.get("error")) for e in data.get("errors", [])}
    return ok, errs


# --------------------------------------------------------------------- orchestration
def looks_us_only(job: Job) -> bool:
    return bool(_US_ONLY.search(f"{job.title}\n{job.description[:1500]}"))


async def discover(
    client,
    cfg: dict,
    known: dict[str, bool],
    relevant: Callable[[Job], bool],
    info: Optional[dict] = None,
    sleep: Callable = asyncio.sleep,
) -> list[Job]:
    """Search -> pre-filter -> fetch details for new jobs (capped) -> drop dead/stale -> return Jobs.

    known: {url: has_details} for jobs already stored, so we never re-fetch a job we already understand.
    relevant: the pipeline's title/location filter (location may be unknown for previews: then it's lenient).
    info: optional dict filled with counters for the run log.
    """
    info = info if info is not None else {}
    queries: list[str] = cfg.get("queries", [])
    pages = int(cfg.get("pages", 2))
    max_fetch = int(cfg.get("max_fetch", 60))
    max_age_days = int(cfg.get("max_age_days", 90))
    counters = {"queries": len(queries), "found": 0, "new": 0, "fetched": 0, "dead": 0, "stale": 0,
                "us_only": 0, "preview_only": 0, "search_errors": 0}

    found: dict[str, Job] = {}
    for q in queries:
        for page in range(pages):
            try:
                batch = await search(client, q, page)
            except Exception as e:  # one bad query must not sink the others
                counters["search_errors"] += 1
                info.setdefault("errors", []).append(f"{q[:50]}… p{page}: {type(e).__name__}")
                if "401" in str(e) or "403" in str(e):
                    raise RuntimeError("TinyFish rejected the API key (check TINYFISH_API_KEY)") from e
                break
            if not batch:
                break
            for j in batch:
                found.setdefault(j.url, j)
            await sleep(0.25)
    counters["found"] = len(found)
    info["seen_urls"] = list(found)

    # Keep only engineering-looking titles; known-good jobs are skipped (already have details).
    fresh = [j for j in found.values() if not known.get(j.url) and _title_ok(j, relevant)]
    counters["new"] = len(fresh)
    to_fetch, previews = fresh[:max_fetch], fresh[max_fetch:]

    out: list[Job] = []
    for i in range(0, len(to_fetch), 10):
        chunk = to_fetch[i : i + 10]
        try:
            ok, errs = await fetch_details(client, [j.url for j in chunk])
        except Exception as e:
            info.setdefault("errors", []).append(f"fetch batch: {type(e).__name__}: {e}")
            previews.extend(chunk)
            continue
        counters["fetched"] += len(ok)
        for j in chunk:
            d = ok.get(j.url)
            if d is None:                      # fetch failed (bot_blocked/timeout): keep as preview, retry next run
                previews.append(j)
                continue
            if d.get("dead"):
                counters["dead"] += 1
                continue
            if d.get("posted_at") and _age_days(d["posted_at"]) > max_age_days:
                counters["stale"] += 1
                continue
            apply_detail(j, d)
            if looks_us_only(j):
                counters["us_only"] += 1
                continue
            out.append(j)

    # Previews are only worth keeping when the search result already told us the location is usable.
    for j in previews:
        if j.location and looks_us_only(j) is False:
            out.append(j)
            counters["preview_only"] += 1
    info.update(counters)
    return out


def _title_ok(job: Job, relevant: Callable[[Job], bool]) -> bool:
    """Lenient pre-filter: title must look like engineering; location is checked only if the search gave one."""
    if job.location:
        return relevant(job)
    probe = Job(**{**job.__dict__, "location": "Remote"})      # unknown location: judge by title alone
    return relevant(probe)


def _age_days(iso: str) -> float:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 86400
    except ValueError:
        return 0.0
