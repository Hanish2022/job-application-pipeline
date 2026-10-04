import pytest

from app.matcher import classify_level, score_job
from app.skills import count_skills, find_skills


@pytest.mark.parametrize("title,desc,expected", [
    ("Software Engineer Intern", "", "intern"),
    ("Summer Internship - Backend", "", "intern"),
    ("Graduate Software Engineer", "", "entry"),
    ("Junior Full-Stack Developer", "", "entry"),
    ("SDE I - Frontend", "", "entry"),
    ("Software Engineer - I", "", "entry"),
    ("Associate Software Engineer", "", "entry"),
    ("Senior Backend Engineer", "", "senior"),
    ("Staff Software Engineer", "", "senior"),
    ("Engineering Manager", "", "senior"),
    ("Software Engineer III", "", "senior"),
    ("SDE 3", "", "senior"),
    ("Software Engineer II", "", "mid"),
    ("Backend Engineer", "5+ years of experience with Java", "senior"),
    ("Backend Engineer", "3 years of professional experience", "mid"),
    ("Backend Engineer", "0-2 years of experience", "entry"),
    ("Backend Engineer", "We welcome freshers and recent graduates", "entry"),
    ("Backend Engineer", "Build APIs.", "unknown"),
    ("Full Stack Developer (0-2 years)", "", "entry"),
    ("Backend Engineer - 5+ yrs", "", "senior"),
    ("Engineer (3-5 years)", "", "mid"),
    ("SDE - I", "", "entry"),
    ("Developer in Test", "", "unknown"),
])
def test_classify_level(title, desc, expected):
    assert classify_level(title, desc) == expected


def test_classify_level_uses_source_hint_only_when_title_is_silent():
    assert classify_level("Backend Engineer", "", hint='["Entry-level", "Mid-level"]') == "entry"
    assert classify_level("Senior Backend Engineer", "", hint='["Entry-level"]') == "senior"


def test_classify_level_max_years_wins():
    assert classify_level("Engineer", "1 year of experience preferred, 6+ years experience required") == "senior"


def test_skill_detection_boundaries():
    s = find_skills("We use Java, JavaScript, C++, C#, Node.js, MySQL and CI/CD. Not javascripty.")
    assert {"java", "javascript", "c++", "c#", "node", "mysql", "ci/cd"} <= s
    assert "sql" not in find_skills("mysql only")      # no partial-word match
    assert "java" not in find_skills("javascript only")
    assert "go" not in count_skills("let's go to market")
    assert count_skills("React, react.js and React") ["react"] == 3


def _score(profile, title, desc="", india=True, remote=False, **kw):
    return score_job(title, desc, is_india=india, remote=remote, profile=profile, **kw)


def test_ideal_fresher_job_scores_high(profile):
    s = _score(profile, "Full Stack Developer (0-2 years)", "React, Node.js, Express, MongoDB. 0-2 years of experience.")
    assert s.score >= 90 and s.level == "entry"
    assert {"react", "node"} <= set(s.matched)
    assert any("Title fits" in r for r in s.reasons)


def test_senior_role_is_capped_low(profile):
    s = _score(profile, "Senior Staff Backend Engineer", "React Node MongoDB Express 10+ years of experience")
    assert s.level == "senior" and s.score <= 25


def test_mid_role_is_capped(profile):
    s = _score(profile, "Software Engineer II", "React Node MongoDB Express JavaScript")
    assert s.level == "mid" and s.score <= 55


def test_non_tech_role_is_capped(profile):
    s = _score(profile, "Account Executive", "React Node Express MongoDB JavaScript")
    assert s.score <= 20


def test_internship_scores_well_when_wanted(profile):
    s = _score(profile, "Software Development Engineer Intern", "React and Node.js")
    assert s.level == "intern" and s.score >= 65
    profile = dict(profile, wants_internships=False)
    assert _score(profile, "Software Development Engineer Intern", "React and Node.js").score < s.score


def test_location_preference_matters(profile):
    base = dict(title="Software Engineer I", desc="React")
    inside = _score(profile, base["title"], base["desc"], india=True).score
    outside = _score(profile, base["title"], base["desc"], india=False, remote=False).score
    assert inside - outside == 5
    only_india = dict(profile, locations=["india"])
    assert _score(only_india, base["title"], base["desc"], india=False, remote=True).score == outside


def test_related_skill_gets_partial_credit(profile):
    ts = _score(profile, "Software Engineer I", "TypeScript")          # typescript ~ javascript
    none = _score(profile, "Software Engineer I", "Cobol")
    assert ts.score > none.score and ts.matched == []


def test_exclude_keywords_force_low_score(profile):
    p = dict(profile, exclude_keywords=["intern"])
    s = _score(p, "Software Engineer Intern", "React Node")
    assert s.score <= 5 and any("Excluded" in r for r in s.reasons)


def test_experienced_profile_prefers_senior(profile):
    p = dict(profile, level="senior")
    assert _score(p, "Senior Backend Engineer", "Node Express MongoDB").score > _score(p, "Backend Intern", "Node Express MongoDB").score


def test_score_is_bounded_and_deterministic(profile):
    a = _score(profile, "Full Stack Developer", "react " * 500)
    b = _score(profile, "Full Stack Developer", "react " * 500)
    assert a == b and 0 <= a.score <= 100 and len(a.reasons) <= 5


# ------------------------------------------------------- scores must spread out instead of piling up at 100
def test_a_strong_match_is_not_automatically_a_perfect_100(profile):
    s = _score(profile, "Full Stack Developer (0-2 years)", "React, Node.js, Express, MongoDB, MySQL, JavaScript, Git. 0-2 years of experience.")
    assert 90 <= s.score <= 99, s.score


def test_more_of_your_skills_scores_higher_with_diminishing_returns(profile):
    base = dict(title="Software Engineer I")
    scores = [_score(profile, base["title"], d).score for d in
              ("React", "React Node.js", "React Node.js Express", "React Node.js Express MongoDB", "React Node.js Express MongoDB MySQL JavaScript")]
    assert scores == sorted(scores) and len(set(scores)) == len(scores), scores            # strictly increasing: no ties
    gains = [b - a for a, b in zip(scores, scores[1:])]
    assert gains[0] > gains[-1] > 0, gains                                                 # each extra skill helps a bit less


def test_coverage_matters_a_job_asking_for_many_things_you_lack_scores_lower(profile):
    focused = _score(profile, "Software Engineer I", "React, Node.js, Express.")
    sprawling = _score(profile, "Software Engineer I", "React, Node.js, Express, Kubernetes, Kafka, Golang, Rust, Terraform, GraphQL, Redis, Angular.")
    assert focused.score > sprawling.score, (focused.score, sprawling.score)               # same matches, but far less of the job is yours


def test_generic_skills_count_less_than_real_ones(profile):
    real = _score(profile, "Software Engineer I", "React Node.js Express")
    generic = _score(profile, "Software Engineer I", "Git API REST HTML CSS Agile")
    assert real.score > generic.score


def test_breakdown_adds_up_to_the_score_and_respects_caps(profile):
    s = _score(profile, "Full Stack Developer (0-2 years)", "React, Node.js, Express, MongoDB. 0-2 years of experience.")
    parts = s.parts
    assert parts["cap"] is None
    assert [parts[k][1] for k in ("skills", "role", "level", "location")] == [50, 25, 20, 5]
    assert all(0 <= parts[k][0] <= parts[k][1] for k in ("skills", "role", "level", "location"))
    assert round(sum(parts[k][0] for k in ("skills", "role", "level", "location"))) == s.score
    senior = _score(profile, "Senior Backend Engineer", "React Node.js Express MongoDB 8+ years of experience")
    assert senior.parts["cap"] == 25 and senior.score == 25
    assert sum(senior.parts[k][0] for k in ("skills", "role", "level", "location")) > 25       # the cap, not the parts, is why it is low
    assert _score(profile, "Account Executive", "React").parts["cap"] == 20
    assert _score({**profile, "exclude_keywords": ["intern"]}, "Software Engineer Intern", "React").parts["cap"] == 5


def test_no_realistic_job_reaches_100(profile):
    everything = "React Node.js Express MongoDB MySQL SQL JavaScript Python Java Docker AWS Git REST API HTML CSS Tailwind JWT " * 3
    assert _score(profile, "Full Stack Developer (0-2 years)", everything).score <= 99
