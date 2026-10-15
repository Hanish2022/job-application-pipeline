"""Skills-gap insight: which technologies keep showing up in the jobs that suit you best, but are not on your resume.

Plain counting, no AI: take the best-matching jobs you still have to act on, find the skills each one asks for (app/skills.py),
and report the ones missing from your profile in order of how many of those jobs want them.
"""
from __future__ import annotations

from .skills import GENERIC, RELATED, count_skills

MAX_GAPS = 12
MAX_HAVE = 8
MIN_JOBS = 2           # a skill must appear in at least this many jobs to be worth mentioning


def skill_gaps(rows, profile: dict | None) -> dict:
    """rows: objects with title/description/tags/department. Returns the gaps (wanted, not on your resume) and what you already have."""
    mine = set((profile or {}).get("skills") or [])
    total = len(rows)
    counts: dict[str, int] = {}
    for r in rows:
        text = f"{r['title']}\n{r['title']}\n{r['tags']}\n{r['department']}\n{r['description']}"
        for skill in count_skills(text):
            counts[skill] = counts.get(skill, 0) + 1

    def entry(skill: str) -> dict:
        n = counts[skill]
        close = sorted(r for r in RELATED.get(skill, []) if r in mine)           # something you know that is next door to it
        return {"skill": skill, "jobs": n, "share": round(n / total, 3) if total else 0.0, "close_to": close}

    gaps = [entry(s) for s in counts if s not in mine and s not in GENERIC and counts[s] >= MIN_JOBS]
    have = [entry(s) for s in counts if s in mine and s not in GENERIC]
    gaps.sort(key=lambda g: (-g["jobs"], g["skill"]))
    have.sort(key=lambda g: (-g["jobs"], g["skill"]))
    return {"jobs": total, "gaps": gaps[:MAX_GAPS], "have": have[:MAX_HAVE]}
