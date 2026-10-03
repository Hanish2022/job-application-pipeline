"""Skill vocabulary used both to read the resume and to read job descriptions."""
from __future__ import annotations

import re

# canonical name -> aliases (lower-case). "related" lets a near-miss earn partial credit.
SKILLS: dict[str, list[str]] = {
    "javascript": ["javascript", "js", "es6", "ecmascript"],
    "typescript": ["typescript", "ts"],
    "java": ["java", "core java", "j2ee", "jdk"],
    "python": ["python", "python3"],
    "c++": ["c++", "cpp"],
    "sql": ["sql", "rdbms", "relational database", "relational databases"],
    "react": ["react", "react.js", "reactjs", "react js"],
    "nextjs": ["next.js", "nextjs", "next js"],
    "redux": ["redux"],
    "html": ["html", "html5"],
    "css": ["css", "css3", "sass", "scss"],
    "tailwind": ["tailwind", "tailwind css", "tailwindcss"],
    "node": ["node", "node.js", "nodejs", "node js"],
    "express": ["express", "express.js", "expressjs"],
    "rest": ["rest", "restful", "rest api", "rest apis", "restful api", "restful apis", "web services"],
    "graphql": ["graphql"],
    "mongodb": ["mongodb", "mongo", "mongoose", "nosql"],
    "mysql": ["mysql", "mariadb"],
    "postgres": ["postgres", "postgresql", "psql"],
    "redis": ["redis"],
    "aws": ["aws", "amazon web services", "ec2", "s3", "lambda"],
    "docker": ["docker", "containers", "containerization"],
    "kubernetes": ["kubernetes", "k8s"],
    "git": ["git", "github", "gitlab", "version control"],
    "jwt": ["jwt", "oauth", "authentication", "authorization"],
    "linux": ["linux", "unix", "bash", "shell scripting"],
    "dsa": ["data structures", "algorithms", "dsa", "problem solving", "competitive programming"],
    "oop": ["oop", "oops", "object oriented", "object-oriented"],
    "microservices": ["microservices", "microservice"],
    "ci/cd": ["ci/cd", "cicd", "jenkins", "github actions"],
    "testing": ["unit testing", "jest", "mocha", "junit", "pytest", "test automation", "tdd"],
    "django": ["django"],
    "flask": ["flask", "fastapi"],
    "spring": ["spring", "spring boot", "springboot"],
    "golang": ["golang"],
    "rust": ["rust"],
    "c#": ["c#", ".net", "dotnet", "asp.net"],
    "php": ["php", "laravel"],
    "android": ["android", "kotlin"],
    "ios": ["swift", "swiftui"],
    "react native": ["react native"],
    "vue": ["vue", "vue.js", "vuejs"],
    "angular": ["angular", "angularjs"],
    "machine learning": ["machine learning", "ml", "deep learning", "tensorflow", "pytorch"],
    "genai": ["genai", "generative ai", "llm", "llms", "prompt engineering", "openai"],
    "api": ["api", "apis", "backend", "back-end", "back end"],
    "agile": ["agile", "scrum"],
    "postman": ["postman"],
    "websocket": ["websocket", "websockets", "socket.io"],
    "firebase": ["firebase"],
    "kafka": ["kafka", "rabbitmq", "message queue"],
}

# A near-miss gets 50% credit when the candidate knows a related skill.
RELATED: dict[str, list[str]] = {
    "typescript": ["javascript"],
    "nextjs": ["react"],
    "redux": ["react"],
    "react native": ["react"],
    "vue": ["react", "javascript"],
    "angular": ["react", "javascript"],
    "postgres": ["sql", "mysql"],
    "mysql": ["sql"],
    "sql": ["mysql", "postgres"],
    "flask": ["python"],
    "django": ["python"],
    "spring": ["java"],
    "kubernetes": ["docker"],
    "graphql": ["rest"],
    "ci/cd": ["git", "docker"],
    "redis": ["mongodb", "mysql"],
    "firebase": ["mongodb"],
    "websocket": ["node"],
}

# Skills too generic to drive a match on their own (they still count a little).
GENERIC = {"api", "agile", "git", "oop", "dsa", "rest", "html", "css", "linux", "testing"}


def _compile(alias: str) -> re.Pattern:
    # Boundaries that understand c++, c#, node.js, ci/cd (plain \b breaks on those).
    return re.compile(r"(?<![a-z0-9+#.])" + re.escape(alias) + r"(?![a-z0-9+#]|\.[a-z0-9])", re.I)


_PATTERNS: dict[str, list[re.Pattern]] = {
    skill: [_compile(a) for a in aliases] for skill, aliases in SKILLS.items()
}


def count_skills(text: str) -> dict[str, int]:
    """Return {canonical_skill: mention_count} for every vocabulary skill found in text."""
    found: dict[str, int] = {}
    if not text:
        return found
    low = text.lower()
    for skill, pats in _PATTERNS.items():
        n = sum(len(p.findall(low)) for p in pats)
        if n:
            found[skill] = n
    return found


def find_skills(text: str) -> set[str]:
    return set(count_skills(text))
