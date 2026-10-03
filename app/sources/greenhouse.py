"""Greenhouse public job-board API: https://boards-api.greenhouse.io/v1/boards/{token}/jobs"""
from __future__ import annotations

from .. import config
from ..models import Job
from ..textutil import html_to_text, to_iso
from .http import get_json

NAME = "greenhouse"
URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def parse(data: dict, token: str) -> list[Job]:
    jobs: list[Job] = []
    for item in data.get("jobs", []):
        depts = [d.get("name", "") for d in item.get("departments") or [] if d.get("name")]
        jobs.append(
            Job(
                source=NAME,
                external_id=str(item.get("id", "")),
                title=(item.get("title") or "").strip(),
                company=(item.get("company_name") or token).strip(),
                url=item.get("absolute_url") or "",
                location=((item.get("location") or {}).get("name") or "").strip(),
                description=html_to_text(item.get("content"), config.MAX_DESCRIPTION_CHARS),
                posted_at=to_iso(item.get("first_published") or item.get("updated_at")),
                department=", ".join(depts),
                board=token,
            )
        )
    return [j for j in jobs if j.is_valid()]


async def fetch(client, token: str) -> list[Job]:
    data = await get_json(client, URL.format(token=token), params={"content": "true"})
    return parse(data, token)
