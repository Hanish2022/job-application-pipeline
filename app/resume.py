"""Turn a resume PDF/text into a structured, user-editable profile."""
from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path

from .skills import count_skills

DEFAULT_LOCATIONS = ["india", "remote"]

# Role phrases matched against job titles. Derived from the skills found + always-on generics.
GENERIC_ROLES = ["software engineer", "software developer", "sde", "developer", "programmer", "engineer"]
ROLE_RULES: list[tuple[set[str], list[str]]] = [
    ({"react", "node", "express", "mongodb"}, ["full stack", "fullstack", "full-stack", "mern", "web developer"]),
    ({"react", "javascript", "html", "css", "nextjs", "tailwind"}, ["frontend", "front end", "front-end", "ui engineer", "react"]),
    ({"node", "express", "java", "python", "sql", "mysql", "mongodb", "api", "rest"}, ["backend", "back end", "back-end", "api engineer", "node"]),
    ({"java"}, ["java developer"]),
    ({"python"}, ["python developer"]),
]


class ResumeError(Exception):
    pass


def extract_text(path: str | Path) -> str:
    """PDF/TXT/MD -> text. Uses pypdf, falls back to the poppler `pdftotext` binary."""
    p = Path(path).expanduser()
    if not p.exists():
        raise ResumeError(f"Resume not found: {p}")
    if p.suffix.lower() in (".txt", ".md"):
        return p.read_text(errors="ignore")
    if p.suffix.lower() != ".pdf":
        raise ResumeError("Unsupported resume type (use PDF, TXT or MD)")
    text = ""
    try:
        from pypdf import PdfReader

        text = "\n".join((page.extract_text() or "") for page in PdfReader(str(p)).pages)
    except Exception:
        text = ""
    if len(text.strip()) < 50:
        try:
            text = subprocess.run(
                ["pdftotext", "-layout", str(p), "-"], capture_output=True, text=True, timeout=30, check=True
            ).stdout
        except (OSError, subprocess.SubprocessError) as e:
            raise ResumeError(f"Could not read PDF text: {e}") from e
    if len(text.strip()) < 50:
        raise ResumeError("Resume has no extractable text (scanned image?)")
    return text


def _grad_year(text: str) -> int | None:
    years = [int(m.group(2)) for m in re.finditer(r"\b(20\d\d)\s*[–\-—to]+\s*(20\d\d)\b", text)]
    return max(years) if years else None


def _experience_years(text: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:professional\s+|work\s+)?experience", text, re.I)
    return float(m.group(1)) if m else 0.0


def _is_contact_line(line: str) -> bool:
    """Phone / email / profile-link lines must never be stored as the 'headline'."""
    return bool(re.search(r"@|\+?\d[\d\s\-()]{7,}|linkedin|github|https?://", line, re.I))


def build_profile(text: str, source_file: str = "") -> dict:
    counts = count_skills(text)
    if not counts:
        raise ResumeError("No recognisable skills found in the resume")
    # Weight = 1 for a passing mention, 1.5 for skills the resume leans on (3+ mentions).
    weights = {s: (1.5 if n >= 3 else 1.0) for s, n in counts.items()}
    skills = sorted(counts, key=lambda s: (-counts[s], s))

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    name = lines[0] if lines and len(lines[0]) < 60 and not re.search(r"[@\d]", lines[0]) else ""
    headline = lines[1] if len(lines) > 1 and "|" in lines[1] and not _is_contact_line(lines[1]) else ""

    roles = list(GENERIC_ROLES)
    have = set(skills)
    for needed, phrases in ROLE_RULES:
        if have & needed:
            roles += [p for p in phrases if p not in roles]

    grad = _grad_year(text)
    years = _experience_years(text)
    this_year = datetime.now().year
    if years >= 5:
        level = "senior"
    elif years >= 2.5:
        level = "mid"
    else:
        level = "entry"
    return {
        "name": name,
        "headline": headline,
        "skills": skills,
        "skill_weights": weights,
        "target_roles": roles,
        "exclude_keywords": [],
        "level": level,
        "wants_internships": bool(grad and grad >= this_year) or level == "entry",
        "graduation_year": grad,
        "years_experience": years,
        "locations": list(DEFAULT_LOCATIONS),
        "source_file": source_file,
    }


def profile_from_file(path: str | Path) -> dict:
    return build_profile(extract_text(path), source_file=str(path))
