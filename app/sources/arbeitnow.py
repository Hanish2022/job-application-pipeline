"""Arbeitnow public job-board API (Europe-centric, many remote roles)."""
from __future__ import annotations

from .. import config
from ..models import Job
from ..textutil import html_to_text, to_iso
from .http import get_json

NAME = "arbeitnow"
URL = "https://www.arbeitnow.com/api/job-board-api"
BOARD = "arbeitnow"


def parse(data: dict) -> list[Job]:
    jobs: list[Job] = []
    for item in data.get("data", []):
        jobs.append(
            Job(
                source=NAME,
                external_id=str(item.get("slug", "")),
                title=(item.get("title") or "").strip(),
                company=(item.get("company_name") or "").strip(),
                url=item.get("url") or "",
                location=(item.get("location") or "").strip(),
                remote=bool(item.get("remote")),
                description=html_to_text(item.get("description"), config.MAX_DESCRIPTION_CHARS),
                posted_at=to_iso(item.get("created_at")),
                employment_type=", ".join(item.get("job_types") or []),
                tags=[str(t) for t in item.get("tags") or []][:12],
                board=BOARD,
            )
        )
    return [j for j in jobs if j.is_valid()]


async def fetch(client, pages: int = 3) -> list[Job]:
    jobs: list[Job] = []
    for page in range(1, pages + 1):
        data = await get_json(client, URL, params={"page": page})
        batch = parse(data)
        if not batch:
            break
        jobs.extend(batch)
    return jobs
