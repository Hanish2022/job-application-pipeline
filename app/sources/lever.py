"""Lever public postings API: https://api.lever.co/v0/postings/{company}?mode=json"""
from __future__ import annotations

from .. import config
from ..models import Job
from ..textutil import html_to_text, to_iso
from .http import get_json

NAME = "lever"
URL = "https://api.lever.co/v0/postings/{company}"
_INTERVAL = {"per-year-salary": "year", "per-month-salary": "month", "per-week-salary": "week", "per-day-wage": "day",
             "per-hour-wage": "hour", "year": "year", "month": "month", "hour": "hour", "week": "week", "day": "day"}


def parse(data: list, company: str) -> list[Job]:
    jobs: list[Job] = []
    for item in data if isinstance(data, list) else []:
        cats = item.get("categories") or {}
        locations = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
        description = item.get("descriptionPlain") or html_to_text(item.get("description"))
        extra = item.get("additionalPlain") or ""
        lists = "\n".join(
            f"{li.get('text', '')}\n{html_to_text(li.get('content'))}" for li in item.get("lists") or []
        )
        full = "\n\n".join(p for p in (description, lists, extra) if p)
        salary, s_min, s_max, s_cur, s_period = "", None, None, "", "year"
        rng = item.get("salaryRange")
        if isinstance(rng, dict) and rng.get("min"):
            s_min, s_max, s_cur = rng.get("min"), rng.get("max"), rng.get("currency") or ""
            s_period = _INTERVAL.get(str(rng.get("interval", "")).lower(), "year")
            salary = f"{s_cur} {s_min}–{s_max} {rng.get('interval', '')}".strip()
        jobs.append(
            Job(
                source=NAME,
                external_id=str(item.get("id", "")),
                title=(item.get("text") or "").strip(),
                company=company.replace("-", " ").title(),
                url=item.get("hostedUrl") or item.get("applyUrl") or "",
                location=" / ".join(locations),
                remote=(item.get("workplaceType") or "").lower() == "remote",
                description=full[: config.MAX_DESCRIPTION_CHARS],
                posted_at=to_iso(item.get("createdAt")),
                salary=salary, salary_min=s_min, salary_max=s_max, salary_currency=s_cur, salary_period=s_period,
                department=cats.get("team") or cats.get("department") or "",
                employment_type=cats.get("commitment") or "",
                board=company,
            )
        )
    return [j for j in jobs if j.is_valid()]


async def fetch(client, company: str) -> list[Job]:
    data = await get_json(client, URL.format(company=company), params={"mode": "json"})
    return parse(data, company)
