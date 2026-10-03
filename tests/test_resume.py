from pathlib import Path

import pytest

from app.resume import ResumeError, build_profile, extract_text, profile_from_file
from tests.conftest import RESUME_TEXT

REAL_RESUME = Path.home() / "Downloads" / "new_resume.pdf"


def test_build_profile_from_text(profile):
    assert profile["name"] == "Jane Doe"
    assert profile["headline"].startswith("Full Stack Developer")
    assert {"react", "node", "mongodb", "express", "javascript", "java", "python", "sql", "mysql", "tailwind"} <= set(profile["skills"])
    assert profile["level"] == "entry" and profile["wants_internships"] is True and profile["graduation_year"] == 2027
    assert profile["locations"] == ["india", "remote"]
    assert "full stack" in profile["target_roles"] and "software engineer" in profile["target_roles"]


def test_skills_mentioned_often_get_more_weight(profile):
    assert profile["skill_weights"]["react"] == 1.5
    assert profile["skill_weights"]["python"] == 1.0


def test_experienced_resume_level():
    text = RESUME_TEXT + "\nI have 6 years of professional experience in backend."
    assert build_profile(text)["level"] == "senior"
    assert build_profile(RESUME_TEXT + "\n3 years of experience in web.")["level"] == "mid"


def test_resume_without_skills_is_rejected():
    with pytest.raises(ResumeError):
        build_profile("Just some prose about my love of gardening and hiking.")


def test_extract_text_txt_and_errors(tmp_path):
    f = tmp_path / "r.txt"
    f.write_text(RESUME_TEXT)
    assert "React" in extract_text(f)
    with pytest.raises(ResumeError):
        extract_text(tmp_path / "missing.pdf")
    bad = tmp_path / "r.docx"
    bad.write_text("x")
    with pytest.raises(ResumeError):
        extract_text(bad)
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 not really a pdf")
    with pytest.raises(ResumeError):
        extract_text(broken)


@pytest.mark.skipif(not REAL_RESUME.exists(), reason="no resume at ~/Downloads/new_resume.pdf")
def test_real_resume_pdf_parses():
    p = profile_from_file(REAL_RESUME)                 # whatever resume the developer has locally
    assert p["skills"] and p["level"] in {"entry", "mid", "senior"}
    assert p["locations"] == ["india", "remote"] and "@" not in str(p)   # no contact details leak into the profile


def test_contact_line_is_never_stored_as_headline():
    text = "Sam Lee\n+91 98765 43210 | sam@example.com | LinkedIn | Github\nSkills: React, Node.js, MongoDB, Express"
    prof = build_profile(text)
    assert prof["name"] == "Sam Lee" and prof["headline"] == ""
    assert "@" not in str(prof) and "98765" not in str(prof)
