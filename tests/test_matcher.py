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


# ------------------------------------------------------------------ the fast skill scanner must answer exactly like the regex reference
import random  # noqa: E402

from app import skills as _skills  # noqa: E402


@pytest.mark.parametrize("text", [
    "", "   ", "React", "react.js and ReactJS", "React,react;React", "(react)", "react-native", "reactive", "preact", "xreact",
    "node.js", "node.", "node.x", "node.js.", "nodejs/node", "c++", "c++11", "c#", "c# and c++", "ci/cd pipelines", "ci/cd.", "go", "let's go to market",
    "java javascript", "javascript only", "mysql only", "postgresql", "sql, SQL; Sql", "js ts es6", "next.js nextjs", "web3", "k8s", "AWS S3", "aws.",
    "react\nreact\r\nreact", "ÉCOLE react école", "react" * 3, "react react.", "react.2", ".react", "+react", "#react", "react+", "react#",
])
def test_fast_skill_scanner_matches_the_regex_reference(text):
    assert _skills.count_skills(text) == _skills.count_skills_regex(text)


def test_fast_skill_scanner_matches_the_regex_reference_on_random_text():
    rng = random.Random(1234)
    aliases = [a for al in _skills.SKILLS.values() for a in al]
    glue = ["", " ", ".", "..", "+", "#", "-", "/", ",", "(", ")", "a", "9", "x.", ".y", "\n", "é", "_"]
    for _ in range(3000):
        text = "".join(rng.choice(aliases) + rng.choice(glue) for _ in range(rng.randint(1, 8)))
        if rng.random() < .3:
            text = text.upper()
        assert _skills.count_skills(text) == _skills.count_skills_regex(text), repr(text)


def test_skill_scanning_is_fast_enough_that_rescoring_the_whole_list_takes_seconds_not_half_a_minute():
    import time
    doc = ("We are hiring a full stack engineer to build with React, Node.js, TypeScript, Docker and AWS. " * 8 + "Lorem ipsum dolor sit amet " * 150)
    start = time.perf_counter()
    for _ in range(300):
        _skills.count_skills(doc)
    assert time.perf_counter() - start < 3.0          # ~0.1 s on a laptop; the old regex scan needed ~8 s for the same work


# ------------------------------------------------------------------ a posting that contradicts itself about experience
from app.matcher import classify_level, experience_conflict  # noqa: E402

KSHM = ("Experience: 2 years of exp.\n\nLocation: Gurugram (On-site)   Type: Full-Time   Experience: 3–5 Years\n\nAbout Kshm\n\nAt Kshm, we're building "
        "a D2C lifestyle brand. We're looking for a Full Stack Developer who enjoys solving problems, using React, Node.js and MongoDB.")


@pytest.mark.parametrize("desc,expected", [
    (KSHM, (2, 3)),
    ("Experience: 1 year of exp.\n\nWe need 4 years of experience with Java.", (1, 4)),
    ("Experience: 2 years of exp.\n\nMinimum 3+ years experience in backend.", (2, 3)),
    ("No experience required.\n\nYou should have 3 years of experience.", (0, 3)),
    ("Experience: 2 years of exp.\n\n5+ years of experience required", None),            # 5+ is not a near miss: the text wins (senior)
    ("Experience: 2 years of exp.\n\nExperience: 2 years preferred", None),                 # no contradiction
    ("Experience: 3 years of exp.\n\n3-5 years of experience", None),                       # the header itself already says 3+
    ("Experience: 2 years of exp.\n\nWe love people who ship. Sunny office.", None),        # text asks for nothing
    ("Experience: 2 years of exp.\n\nWe have been around for 4 years and love coffee.", None),   # years, but not about experience
    ("Location: Pune\n\nExperience: 3-5 years", None),                                      # no header line
    ("", None), (None, None),
])
def test_experience_conflict(desc, expected):
    assert experience_conflict(desc) == expected


def test_a_self_contradicting_posting_is_not_ranked_as_mid_level():
    assert classify_level("Full Stack Developer", KSHM) == "unknown"
    assert classify_level("Full Stack Developer", KSHM.replace("Experience: 2 years of exp.", "Experience: 3 years of exp.")) == "mid"     # no conflict: old rule


@pytest.mark.parametrize("title,level", [("Senior Full Stack Developer", "senior"), ("Full Stack Developer II", "mid"), ("Junior Full Stack Developer", "entry"),
                                         ("Full Stack Developer Intern", "intern")])
def test_the_title_still_settles_it_when_it_can(title, level):
    assert classify_level(title, KSHM) == level


def test_an_unclear_job_is_shown_uncapped_and_says_why(profile):
    s = score_job("Full Stack Developer", KSHM, is_india=True, remote=False, profile=profile)
    assert s.level == "unknown" and s.parts["cap"] is None and s.score > 55
    assert s.reasons[0].startswith("Matches your skills") and any("Experience unclear: the header says 2 years, the description asks for 3+" in r for r in s.reasons)
    old = score_job("Full Stack Developer", KSHM.replace("Experience: 2 years of exp.", "Experience: 3 years of exp."), is_india=True, remote=False, profile=profile)
    assert old.level == "mid" and old.score <= 55 and not any("unclear" in r for r in old.reasons)
