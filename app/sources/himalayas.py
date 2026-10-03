"""Himalayas public remote-jobs API (https://himalayas.app/api).

Terms: free for anyone; link back to the Himalayas URL and credit Himalayas as the source
(we store applicationLink, which is a himalayas.app URL, and show the source on every card).
Search endpoint supports seniority + country filters, 20 jobs per page, rate-limited (429).
"""
from __future__ import annotations

import asyncio

import httpx

from .. import config
from ..models import Job
from ..textutil import html_to_text, to_iso
from .http import get_json

NAME = "himalayas"
URL = "https://himalayas.app/jobs/api/search"
_PERIOD = {"annual": "year", "yearly": "year", "monthly": "month", "fortnightly": "week", "weekly": "week", "daily": "day", "hourly": "hour"}


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse(data: dict, board: str = "himalayas") -> list[Job]:
    jobs: list[Job] = []
    for item in data.get("jobs", []):
        restrictions = [str(r) for r in item.get("locationRestrictions") or []]
        where = ", ".join(restrictions) if restrictions else "Worldwide"
        lo, hi = item.get("minSalary"), item.get("maxSalary")
        salary = ""
        if lo and hi:
            salary = f"{item.get('currency', '')} {int(float(lo)):,}–{int(float(hi)):,} {item.get('salaryPeriod', '')}".strip()
        seniority = [str(s) for s in item.get("seniority") or []]
        link = item.get("applicationLink") or item.get("guid") or ""
        jobs.append(
            Job(
                source=NAME,
                external_id=str(item.get("guid") or link),
                title=(item.get("title") or "").strip(),
                company=(item.get("companyName") or "").strip(),
                url=link,
                location=f"Remote – {where}",
                remote=True,
                description=html_to_text(item.get("description") or item.get("excerpt"), config.MAX_DESCRIPTION_CHARS),
                posted_at=to_iso(item.get("pubDate")),
                salary=salary, salary_min=_f(lo), salary_max=_f(hi), salary_currency=item.get("currency") or "",
                salary_period=_PERIOD.get(str(item.get("salaryPeriod") or "annual").lower(), "year"),
                department=", ".join(item.get("parentCategories") or [])[:120],
                employment_type=item.get("employmentType") or "",
                tags=seniority[:4],
                board=board,
            )
        )
    return [j for j in jobs if j.is_valid()]


async def fetch(client, searches: list[dict], pages: int = 3, delay: float = 0.6) -> list[Job]:
    """Run each filtered search for up to `pages` pages. Stops a search early when a page is short.

    A search the API rejects (HTTP 400) is skipped; the source only fails if *every* search fails."""
    out: list[Job] = []
    failures: list[Exception] = []
    for search in searches:
        for page in range(1, pages + 1):
            params = {k: v for k, v in search.items() if v} | {"page": page, "sort": "recent"}
            try:
                data = await get_json(client, URL, params=params)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 400:
                    failures.append(e)
                    break
                raise
            out.extend(parse(data))
            await asyncio.sleep(delay)
            if len(data.get("jobs", [])) < 20:
                break
    if failures and len(failures) == len(searches):
        raise failures[0]
    return out
