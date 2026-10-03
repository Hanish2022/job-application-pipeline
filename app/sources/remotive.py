"""Remotive public API (remote jobs): https://remotive.com/api/remote-jobs

Remotive asks API users to fetch sparingly (a few times a day) and to link back to the
job URL - we do both (daily cron, and the dashboard links to the original posting)."""
from __future__ import annotations

from .. import config
from ..models import Job
from ..textutil import html_to_text, to_iso
from .http import get_json

NAME = "remotive"
URL = "https://remotive.com/api/remote-jobs"
BOARD = "remote-jobs"


def parse(data: dict) -> list[Job]:
    jobs: list[Job] = []
    for item in data.get("jobs", []):
        location = (item.get("candidate_required_location") or "").strip()
        jobs.append(
            Job(
                source=NAME,
                external_id=str(item.get("id", "")),
                title=(item.get("title") or "").strip(),
                company=(item.get("company_name") or "").strip(),
                url=item.get("url") or "",
                location=f"Remote – {location}" if location else "Remote",
                remote=True,
                description=html_to_text(item.get("description"), config.MAX_DESCRIPTION_CHARS),
                posted_at=to_iso(item.get("publication_date")),
                salary=(item.get("salary") or "").strip(),
                department=item.get("category") or "",
                employment_type=item.get("job_type") or "",
                tags=[str(t) for t in item.get("tags") or []][:12],
                board=BOARD,
            )
        )
    return [j for j in jobs if j.is_valid()]


async def fetch(client, category: str = "software-dev") -> list[Job]:
    data = await get_json(client, URL, params={"category": category})
    return parse(data)
