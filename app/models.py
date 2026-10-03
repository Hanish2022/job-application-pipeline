"""Unified job schema shared by every source."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Job:
    source: str                 # greenhouse | lever | ashby | remotive | arbeitnow | adzuna
    external_id: str            # id inside the source
    title: str
    company: str
    url: str                    # link the user clicks to apply
    location: str = ""
    remote: bool = False
    description: str = ""       # plain text
    posted_at: Optional[str] = None   # ISO-8601 (UTC) when known
    salary: str = ""
    department: str = ""
    employment_type: str = ""
    board: str = ""             # e.g. the greenhouse board token; used for closing stale jobs
    # Optional structured pay (when the source provides numbers rather than text)
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: str = ""
    salary_period: str = "year"   # year | month | week | day | hour
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Sources sometimes return null for optional text fields; the DB columns are NOT NULL.
        for name in ("external_id", "title", "company", "url", "location", "description",
                     "salary", "department", "employment_type", "board", "salary_currency"):
            value = getattr(self, name)
            setattr(self, name, "" if value is None else str(value))
        self.tags = list(self.tags or [])

    def is_valid(self) -> bool:
        return bool(self.title and self.company and self.url and self.external_id)
