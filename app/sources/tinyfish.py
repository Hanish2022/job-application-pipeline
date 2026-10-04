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
from ..textutil import html_to_text, looks_remote, to_iso
from .http import get_json, post_json

SEARCH_URL = "https://api.search.tinyfish.ai"
FETCH_URL = "https://api.fetch.tinyfish.ai"
PREVIEW_TAG = "via-search"

# host -> (source name, posting-URL regex). Only individual postings, never listing/company pages.
SITES: dict[str, tuple[str, re.Pattern]] = {
    "wellfound.com": ("wellfound", re.compile(r"^https?://(?:www\.)?wellfound\.com/jobs/(\d+)", re.I)),
    "workatastartup.com": ("yc", re.compile(r"^https?://(?:www\.)?workatastartup\.com/jobs/(\d+)(?:$|[/?#])", re.I)),
}

GONE = re.compile(r"page[_ ]?not[_ ]?found|not[_ ]?found|\b404\b|\b410\b|gone", re.I)     # definitive: the URL no longer exists
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


_ROLE_PAGE_RE = re.compile(r"^https?://(?:www\.)?wellfound\.com/(?:role|location)/", re.I)
ROLE_PAGE_LIMIT = 10          # URLs per fetch request


def job_links_from_page(result: dict) -> list[Job]:
    """Preview Jobs for every individual Wellfound/YC posting linked from a fetched listing page (title from the URL slug)."""
    links = set(result.get("links") or [])
    links |= set(re.findall(r"\((https?://[^)\s]+)\)", result.get("text") or ""))
    out: dict[str, Job] = {}
    for link in sorted(links):
        ident = identify(link)
        if not ident or ident[0] != "wellfound":          # YC listing pages ignore filters (all return the same default list)
            continue
        source, ext_id, url = ident
        slug = url.rsplit("/", 1)[-1]
        title = re.sub(r"^\d+-", "", slug).replace("-", " ").strip().title()
        if title and url not in out:
            out[url] = Job(source=source, external_id=ext_id, title=title, company="Unknown", url=url, board=source, tags=[PREVIEW_TAG])
    return list(out.values())


async def harvest_role_pages(client, urls: list[str]) -> tuple[list[Job], int]:
    """Fetch Wellfound role/listing pages and return the job postings they link to.  (jobs, pages_ok)"""
    jobs: dict[str, Job] = {}
    ok = 0
    for i in range(0, len(urls), ROLE_PAGE_LIMIT):
        chunk = urls[i : i + ROLE_PAGE_LIMIT]
        data = await post_json(client, FETCH_URL, {"urls": chunk, "format": "markdown", "links": True}, headers=_headers())
        for res in data.get("results", []):
            ok += 1
            for j in job_links_from_page(res):
                jobs.setdefault(j.url, j)
    return list(jobs.values()), ok


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


_SECTION_LABELS = re.compile(
    r"^(?:hires remotely in|job location|remote work policy|company location|visa sponsorship|relocation\w*|skills|preferred \w+|"
    r"collaboration hours|about the job|actively hiring)\b", re.I)


def _after(label: str, text: str) -> str:
    """Value on the line after a label such as 'Job Location' (Wellfound layout). An empty section (whose next
    line is just another label) yields "", never that label."""
    m = re.search(rf"^\s*{re.escape(label)}\s*\n+\s*([^\n]+)", text, re.I | re.M)
    if not m:
        return ""
    value = m.group(1).strip()
    return "" if _SECTION_LABELS.match(value) else value


_WF_SECTION_END = re.compile(r"^(?:#{2,}\s|posted\b|reposted\b|hires remotely in|job location|remote work policy|company location)", re.I)


def _wf_header(text: str) -> dict:
    """Wellfound's job header, e.g.
         # Junior Full-Stack Developer
         * $24k – $28k • No equity
         * |Remote (
           Everywhere
           )
         * |1 year of exp
         * |Full Time
       -> {"work_location": "Remote – Everywhere", "experience": "1 year of exp", "employment_type": "Full Time"} (keys only when found).
    Line scanner (not one big regex): bullets can wrap across several lines."""
    lines = text[:3500].splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith("# ")), None)
    if start is None:
        return {}
    bullets: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if _WF_SECTION_END.match(stripped):
            break
        if line.startswith("* "):
            bullets.append(stripped[2:])
        elif bullets and stripped and line.startswith((" ", "\t")):
            bullets[-1] += " " + stripped                     # indented continuation of a wrapped bullet
        elif stripped and not bullets:
            continue                                          # text before the first bullet (tagline, pay line on YC pages)
        elif stripped:
            break                                             # a normal paragraph after the bullets: the header is over
    out: dict = {}
    for b in bullets:
        b = re.sub(r"\s+", " ", b).strip(" |•")
        if not b:
            continue
        if re.match(r"(remote|onsite|hybrid)\b", b, re.I):
            loc = re.sub(r"\s*\(\s*", " – ", b, count=1)
            loc = re.sub(r"\s*\)\s*", " ", loc)
            loc = re.sub(r"(?<=\s)[.·•|]+(?=\s|\w)|(?<=\w)[.·|]+(?=\s|$)", "", loc)   # decoration like "( . Everywhere. )"
            out["work_location"] = re.sub(r"\s+", " ", loc).strip(" •–-.")
        elif re.search(r"\b(?:years?|yrs?) of exp|no experience required", b, re.I):
            out["experience"] = b
        elif re.match(r"(full[- ]?time|part[- ]?time|contract|internship|intern)\b", b, re.I):
            out["employment_type"] = b
    return out


def parse_detail(text: str) -> dict:
    """Pull structured fields out of a rendered posting (markdown). Missing fields are simply absent."""
    info: dict = {}
    if not text:
        return info
    head = text[:2500]
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if lines:
        first = re.sub(r"\s*[·•]\s*[WSFX]\d{2}$", "", lines[0]).strip()
        at = re.search(r"\bat\s+(.+?)\s*\([WSFX]\d{2}\)\s*$", first)           # YC: "# Role at Company(W21)"
        if at:
            info["company"] = at.group(1).strip()[:80]
        elif not first.startswith("#"):                                           # Wellfound: the first line is the company
            info["company"] = first[:80]
    wf = _wf_header(text)
    hires_in = _after("Hires remotely in", text)
    loc = wf.get("work_location") or ""                                     # Wellfound: "Remote – Everywhere" / "Remote – India" / "Onsite …"
    if loc.lower().startswith("remote") and loc.strip("– ").lower() == "remote" and hires_in:
        loc = f"Remote – {hires_in}"
    if not loc:
        loc = _after("Job Location", text)                                  # Wellfound onsite jobs
    if not loc:
        m = re.search(r"^\s*Location:\s*(.+)$", text, re.I | re.M)         # YC, free-text layout
        loc = m.group(1).strip() if m else ""
    if not loc:
        m = re.search(r"^\s*Location(?![: ])\s*(.+)$", head, re.I | re.M)   # YC, "LocationNew Delhi, DL, IN"
        loc = m.group(1).strip() if m else ""
    yc_exp = ""
    if not loc and not wf.get("work_location"):                              # YC compact header (Wellfound pages always have a bullet header)
        m = _YC_COMPACT.search(head)                                        # YC, "Pune, IndiaFull-time…3+ years"
        if m:
            loc = m.group("loc").strip(" /")
            em = _YC_EXP.search(m.group("rest"))
            yc_exp = em.group(1) if em else ""
    loc = re.sub(r"\s+", " ", loc).strip()
    info["location"] = "" if re.fullmatch(r"[\W_]*", loc) else loc[:160]      # punctuation-only is not a location
    if wf.get("experience"):
        info["experience"] = wf["experience"]
    if wf.get("employment_type"):
        info["employment_type"] = wf["employment_type"]
    policy = _after("Remote Work Policy", text)
    info["remote_policy"] = policy
    m = re.search(r"No experience required|\d+\+?\s*(?:[-–]\s*\d+\s*)?(?:years?|yrs?) of (?:experience|exp)\b", head, re.I)
    if info.get("experience"):
        pass
    elif yc_exp:                                                            # the YC header line beats sentences in the description
        info["experience"] = yc_exp
    elif m:
        info["experience"] = m.group(0)
    elif yc_exp:
        info["experience"] = yc_exp
    else:
        m = re.search(r"^\s*Experience\s*(\d+\+?\s*years?)", head, re.I | re.M)
        if m:
            info["experience"] = m.group(1)
    m = re.search(r"(Internship|Full[- ]?time|Part[- ]?time|Contract)", head, re.I)
    if m and not info.get("employment_type"):
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
    elif looks_remote(loc):                                                 # YC has no separate policy field: "Remote (Worldwide)"
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
                "us_only": 0, "preview_only": 0, "search_errors": 0, "city_label_ignored": 0,
                "search_calls": 0, "paging_stopped": 0, "role_pages_ok": 0, "role_jobs": 0, "gone": 0}

    found: dict[str, Job] = {}
    min_new = int(cfg.get("page_min_new", 2))       # keep paging a query only while a page still brings this many unseen jobs
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
            unseen = [j for j in batch if j.url not in known and j.url not in found]
            for j in batch:
                found.setdefault(j.url, j)
            counters["search_calls"] += 1
            await sleep(0.25)
            if len(unseen) < min_new:                # this page was mostly things we already know: deeper pages won't be fresher
                counters["paging_stopped"] += 1
                break

    role_pages = [u for u in cfg.get("role_pages", []) if _ROLE_PAGE_RE.match(u)]
    if role_pages:
        try:
            listed, ok = await harvest_role_pages(client, role_pages)
            counters["role_pages_ok"] = ok
            counters["role_jobs"] = sum(1 for j in listed if j.url not in found)
            for j in listed:
                found.setdefault(j.url, j)
        except Exception as e:
            info.setdefault("errors", []).append(f"role pages: {type(e).__name__}: {e}")
    counters["found"] = len(found)
    info["seen_urls"] = list(found)

    # Keep only engineering-looking titles; known-good jobs are skipped (already have details).
    fresh = [j for j in found.values() if not known.get(j.url) and _title_ok(j, relevant)]
    fresh.sort(key=fetch_priority)                                   # stable: promising ones first if the fetch cap bites
    fresh = interleave_by_source(fresh)                              # ...but every source gets its fair share of the cap
    counters["new"] = len(fresh)
    counters["city_label_ignored"] = sum(1 for j in fresh if j.location and not relevant(j))   # would have been dropped before this fix
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
        info.setdefault("processed", []).extend(ok)         # pages we successfully read (kept or not)
        for j in chunk:
            d = ok.get(j.url)
            if d is None:
                if GONE.search(errs.get(j.url, "")):      # the posting no longer exists: remember it, never fetch it again
                    info.setdefault("processed", []).append(j.url)
                    counters["gone"] += 1
                else:                                     # bot_blocked / timeout: may work tomorrow, keep as preview
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
        if j.location and relevant(j) and looks_us_only(j) is False:
            out.append(j)
            counters["preview_only"] += 1
    info.update(counters)
    return out


def _title_ok(job: Job, relevant: Callable[[Job], bool]) -> bool:
    """Pre-filter: is the TITLE engineering-like?  The location shown in a search result is deliberately ignored.

    Wellfound labels results with the COMPANY's home city ("Junior Full-Stack Developer at Nexorlio • Las Vegas") even when the
    job is "Remote only - Everywhere", so that label says nothing reliable about where the job can be done. The real location is
    read from the posting itself after fetching (and jobs that turn out to be unusable are remembered, so they cost one fetch)."""
    probe = Job(**{**job.__dict__, "location": "Remote"})
    return relevant(probe)


_EARLY_CAREER = re.compile(r"\b(intern(?:ship)?|junior|jr\.?|graduate|new grad|fresher|entry[- ]level|associate|trainee|sde[- ]?(?:1|i)\b|engineer[- ]?(?:1|i)\b)", re.I)


def interleave_by_source(jobs: list[Job]) -> list[Job]:
    """Round-robin across sources, keeping each source's own (priority) order.  With one shared per-run fetch cap, a source with
    hundreds of candidates (Wellfound + its listing pages) must not use the whole budget and starve the others (YC)."""
    buckets: dict[str, list[Job]] = {}
    for j in jobs:
        buckets.setdefault(j.source, []).append(j)
    order, out, i = list(buckets), [], 0
    while any(buckets.values()):
        for src in order:
            if buckets[src]:
                out.append(buckets[src].pop(0))
    return out


def fetch_priority(job: Job) -> int:
    """Lower = fetch sooner, when the per-run cap means we can't fetch everything.
       0: the search label already says India/remote      1: early-career title      2: everything else"""
    from ..textutil import is_india, looks_remote     # local import: textutil is tiny and has no cycles
    if is_india(job.location) or looks_remote(job.location):
        return 0
    return 1 if _EARLY_CAREER.search(job.title or "") else 2


def _age_days(iso: str) -> float:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 86400
    except ValueError:
        return 0.0
