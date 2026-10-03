"""Ashby public posting API: https://api.ashbyhq.com/posting-api/job-board/{name}"""
from __future__ import annotations

from .. import config
from ..models import Job
from ..textutil import html_to_text, to_iso
from .http import get_json

NAME = "ashby"
URL = "https://api.ashbyhq.com/posting-api/job-board/{name}"


def parse(data: dict, name: str) -> list[Job]:
    jobs: list[Job] = []
    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue
        locs = [item.get("location") or ""] + [
            s.get("location", "") if isinstance(s, dict) else str(s) for s in item.get("secondaryLocations") or []
        ]
        location = " / ".join(dict.fromkeys(l for l in locs if l))
        comp = item.get("compensation") or {}
        jobs.append(
            Job(
                source=NAME,
                external_id=str(item.get("id", "")),
                title=(item.get("title") or "").strip(),
                company=name.replace("-", " ").title(),
                url=item.get("jobUrl") or item.get("applyUrl") or "",
                location=location,
                remote=bool(item.get("isRemote")) or (item.get("workplaceType") or "").lower() == "remote",
                description=(item.get("descriptionPlain") or html_to_text(item.get("descriptionHtml")))[
                    : config.MAX_DESCRIPTION_CHARS
                ],
                posted_at=to_iso(item.get("publishedAt")),
                salary=comp.get("compensationTierSummary", "") if isinstance(comp, dict) else "",
                department=item.get("team") or item.get("department") or "",
                employment_type=item.get("employmentType") or "",
                board=name,
            )
        )
    return [j for j in jobs if j.is_valid()]


async def fetch(client, name: str) -> list[Job]:
    data = await get_json(client, URL.format(name=name), params={"includeCompensation": "true"})
    return parse(data, name)
