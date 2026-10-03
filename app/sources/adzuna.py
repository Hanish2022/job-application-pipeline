"""Adzuna aggregator (optional, needs free API keys: https://developer.adzuna.com).

Set ADZUNA_APP_ID and ADZUNA_APP_KEY in .env to enable. Covers India ("in") and other countries.
"""
from __future__ import annotations

from .. import config
from ..models import Job
from ..textutil import html_to_text, to_iso
from .http import get_json

NAME = "adzuna"
URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
_CURRENCY = {"in": "INR", "us": "USD", "gb": "GBP", "ca": "CAD", "au": "AUD", "sg": "SGD"}


def enabled() -> bool:
    return bool(config.ADZUNA_APP_ID and config.ADZUNA_APP_KEY)


def parse(data: dict, country: str) -> list[Job]:
    jobs: list[Job] = []
    for item in data.get("results", []):
        loc = (item.get("location") or {}).get("display_name", "")
        lo, hi = item.get("salary_min"), item.get("salary_max")
        jobs.append(
            Job(
                source=NAME,
                external_id=str(item.get("id", "")),
                title=html_to_text(item.get("title")),
                company=((item.get("company") or {}).get("display_name") or "").strip(),
                url=item.get("redirect_url") or "",
                location=loc,
                description=html_to_text(item.get("description"), config.MAX_DESCRIPTION_CHARS),
                posted_at=to_iso(item.get("created")),
                salary=f"{int(lo)}–{int(hi)}" if lo and hi else "",
                salary_min=lo, salary_max=hi, salary_currency=_CURRENCY.get(country, "") if lo and hi else "",
                department=(item.get("category") or {}).get("label", ""),
                employment_type=item.get("contract_time") or "",
                board=f"adzuna-{country}",
            )
        )
    return [j for j in jobs if j.is_valid()]


async def fetch(client, country: str, what: str, pages: int = 2, where: str = "") -> list[Job]:
    jobs: list[Job] = []
    for page in range(1, pages + 1):
        params = {
            "app_id": config.ADZUNA_APP_ID,
            "app_key": config.ADZUNA_APP_KEY,
            "what": what,
            "results_per_page": 50,
            "content-type": "application/json",
        }
        if where:
            params["where"] = where
        data = await get_json(client, URL.format(country=country, page=page), params=params)
        batch = parse(data, country)
        if not batch:
            break
        jobs.extend(batch)
    return jobs
