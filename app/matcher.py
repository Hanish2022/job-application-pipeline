"""Deterministic job <-> profile scoring (0-100) with human-readable reasons.

score = skills (0-50) + role/title fit (0-25) + seniority fit (0-20) + location (0-5)
Senior / staff / manager roles are capped so they never surface as strong matches for a fresher.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .skills import GENERIC, RELATED, count_skills

_INTERN = re.compile(r"\b(intern|interns|internship|trainee|apprentice|apprenticeship|co-?op|working student)\b", re.I)
_ENTRY_TITLE = re.compile(
    r"\b(graduate|new grad|fresher|freshers|entry[- ]level|junior|jr\.?|associate|"
    r"sde\s*[-–]?\s*(1|i)\b|software (development )?engineer\s*[-–]?\s*(1|i)\b|engineer\s*[-–]?\s*(1|i)\b|developer\s*[-–]?\s*(1|i)\b|"
    r"level 1|l1|early career|campus|university|fresh graduate)",
    re.I,
)
_SENIOR_TITLE = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|manager|director|head of|head,|vp|vice president|architect|"
    r"distinguished|fellow|chief|president|founding)\b|\b(iii|iv|v)\b\s*$|\b(sde|engineer|developer)[- ]?(3|4|5|iii|iv|v)\b|\bl[4-9]\b",
    re.I,
)
_MID_TITLE = re.compile(r"\b(ii|2)\b\s*$|\b(sde|engineer|developer)[- ]?(2|ii)\b|\bmid[- ]level\b|\bintermediate\b", re.I)
_DESC_ENTRY = re.compile(
    r"\b(freshers?|new grads?|recent graduates?|entry[- ]level|0\s*[-–to]+\s*[12]\s*years?|"
    r"(class|batch) of 202\d|202[4-7] (batch|graduates?)|early[- ]career)\b",
    re.I,
)
_EXP_YEARS = re.compile(r"(\d{1,2})\s*(?:\+|plus)?\s*(?:[-–]|to)?\s*(?:\d{1,2})?\s*\+?\s*(?:years?|yrs?)", re.I)
_TITLE_YEARS = re.compile(r"\b(\d{1,2})\s*(?:\+|plus)?\s*(?:[-–]\s*\d{1,2}\s*\+?)?\s*(?:years?|yrs?)\b", re.I)
_TECH_TITLE = re.compile(
    r"\b(engineer|developer|sde|sdet|swe|programmer|software|full[- ]?stack|back[- ]?end|front[- ]?end|web|devops|sre|"
    r"site reliability|platform|cloud|mobile|android|ios|qa|quality assurance|test automation|data engineer|"
    r"machine learning|ml|ai|security engineer|application|technologist|developer advocate)\b",
    re.I,
)

ENTRY_LEVELS = {"intern", "entry"}


@dataclass
class Score:
    score: int
    level: str
    matched: list[str]
    reasons: list[str]


def _max_required_years(text: str) -> int | None:
    """Largest 'N years' figure that sits next to the word experience (best proxy for seniority)."""
    vals = []
    for m in _EXP_YEARS.finditer(text):
        window = text[max(0, m.start() - 60) : m.end() + 60].lower()
        if "experience" in window or "exp" in window:
            vals.append(int(m.group(1)))
    return max(vals) if vals else None


def classify_level(title: str, description: str = "", hint: str = "") -> str:
    """intern | entry | mid | senior | unknown.  `hint` = source-provided seniority tags, if any."""
    t = title or ""
    if _INTERN.search(t):
        return "intern"
    if _SENIOR_TITLE.search(t):
        return "senior"
    if _ENTRY_TITLE.search(t):
        return "entry"
    if _MID_TITLE.search(t):
        return "mid"
    m = _TITLE_YEARS.search(t)
    if m:  # e.g. "Developer (0-2 years)", "Engineer - 5+ yrs"
        low = int(m.group(1))
        return "senior" if low >= 5 else "mid" if low >= 3 else "entry"
    h = (hint or "").lower()
    if re.search(r"\b(entry|junior|intern|graduate)\b", h):
        return "entry"
    desc = (description or "")[:6000]
    years = _max_required_years(desc)
    if years is not None:
        if years >= 5:
            return "senior"
        if years >= 3:
            return "mid"
        return "entry"
    if _DESC_ENTRY.search(desc):
        return "entry"
    return "unknown"


def _title_hits(title: str, roles: list[str]) -> list[str]:
    low = title.lower()
    return [r for r in roles if r and r.lower() in low]


def score_job(
    title: str,
    description: str,
    *,
    is_india: bool,
    remote: bool,
    profile: dict,
    tags: str = "",
    department: str = "",
) -> Score:
    reasons: list[str] = []
    text = f"{title}\n{title}\n{tags}\n{department}\n{description or ''}"  # title counted twice: it's the strongest signal
    job_skills = count_skills(text)

    weights: dict[str, float] = profile.get("skill_weights") or {}
    mine = set(profile.get("skills") or [])

    # --- skills -----------------------------------------------------------------
    matched: list[str] = []
    gained = 0.0
    for skill in job_skills:
        w = float(weights.get(skill, 1.0))
        if skill in mine:
            matched.append(skill)
            gained += w * (0.4 if skill in GENERIC else 1.0)
        elif any(r in mine for r in RELATED.get(skill, [])):
            gained += 0.5
    skill_pts = 50.0 * min(1.0, gained / 5.0)
    matched.sort(key=lambda s: (s in GENERIC, -weights.get(s, 1.0), s))

    # --- role / title fit -------------------------------------------------------
    tech = bool(_TECH_TITLE.search(title))
    hits = _title_hits(title, profile.get("target_roles") or [])
    specific = [h for h in hits if h not in ("developer", "engineer", "programmer")]
    if specific:
        role_pts = 25.0
        reasons.append(f"Title fits your target role ({specific[0]})")
    elif hits:
        role_pts = 15.0
    elif tech:
        role_pts = 8.0
    else:
        role_pts = 0.0

    # --- seniority --------------------------------------------------------------
    level = classify_level(title, description, hint=tags)
    user_level = profile.get("level", "entry")
    wants_intern = profile.get("wants_internships", True)
    if user_level == "entry":
        if level == "intern":
            level_pts = 20.0 if wants_intern else 8.0
            reasons.append("Internship" if wants_intern else "Internship (not your target)")
        elif level == "entry":
            level_pts = 20.0
            reasons.append("Entry-level / fresher friendly")
        elif level == "unknown":
            level_pts = 5.0
        elif level == "mid":
            level_pts = 0.0
            reasons.append("Asks for mid-level experience")
        else:
            level_pts = 0.0
            reasons.append("Senior role — likely too experienced")
    else:  # experienced candidate: invert the preference lightly
        level_pts = {"senior": 20.0, "mid": 20.0, "unknown": 10.0, "entry": 6.0, "intern": 0.0}[level]

    # --- location ---------------------------------------------------------------
    prefs = set(profile.get("locations") or [])
    loc_ok = (not prefs) or ("india" in prefs and is_india) or ("remote" in prefs and remote)
    loc_pts = 5.0 if loc_ok else 0.0
    if is_india and loc_ok:
        reasons.append("Based in India")
    elif remote and loc_ok:
        reasons.append("Remote-eligible")

    total = skill_pts + role_pts + level_pts + loc_pts

    # --- gates ------------------------------------------------------------------
    if not tech and not hits:
        total = min(total, 20.0)
    if user_level == "entry":
        if level == "senior":
            total = min(total, 25.0)
        elif level == "mid":
            total = min(total, 55.0)
    low_title = title.lower()
    for bad in profile.get("exclude_keywords") or []:
        if bad and bad.lower() in low_title:
            total = min(total, 5.0)
            reasons.append(f"Excluded keyword: {bad}")
            break

    if matched:
        reasons.insert(0, "Matches your skills: " + ", ".join(matched[:6]))
    return Score(int(round(max(0.0, min(100.0, total)))), level, matched[:12], reasons[:5])
