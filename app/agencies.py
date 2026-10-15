"""Spot staffing agencies / body-shops, so the dashboard can hide them.

A body-shop does not build a product: it places you with a client, often on a contract. Their postings crowd out real
product-company jobs. Three signals, strongest first:
  1. the company name says so  ("... Staffing", "... Recruitment", "... Manpower")
  2. the company is on the short list of well-known placement firms and talent marketplaces below
  3. the posting itself says it is hiring on behalf of a client ("our client is looking for ...", "contract-to-hire")

This is a heuristic, not a verdict. It errs on the side of NOT flagging (an unflagged agency is a nuisance; a flagged real
employer is a missed job), and the dashboard shows how many jobs were hidden and lets you show them again.
The list is deliberately small and plain: edit KNOWN below to add or remove companies.
"""
from __future__ import annotations

import re

# Words in a company name that mean "placement / recruiting business".
_NAME = re.compile(
    r"\b(staffing|recruit(?:ment|ments|ing|ers?)|headhunt\w*|manpower|man\s?power|"
    r"placement\s+(?:services|consultants?|agency|solutions)|resourcing|outsourc\w*|"
    r"talent\s+(?:solutions|partners|acquisition|hunt|bridge)|hr\s+(?:solutions|services|consult\w*)|"
    r"consultancy|consultancies|contract\s+(?:staffing|hiring)|workforce\s+solutions)\b",
    re.I,
)

# Well-known placement firms and "we place you with clients" marketplaces (normalised: lower-case, letters and digits only).
KNOWN = {
    # global placement / staffing firms
    "randstad", "adecco", "manpowergroup", "kellyservices", "hays", "michaelpage", "roberthalf", "insightglobal",
    "teksystems", "apexsystems", "collabera", "kforce", "experis", "aquent",
    # India
    "teamlease", "quess", "quesscorp", "pradeepit", "pradeepitconsulting", "xpheno", "careernet",
    # marketplaces that place you with their clients
    "toptal", "andela", "crossover",
}

# The posting says it is hiring for someone else.
_TEXT = re.compile(
    r"on\s+behalf\s+of\s+(?:our|a|one\s+of\s+our)\s+(?:client|customer)s?|"
    r"\bour\s+client\b[^.\n]{0,80}\b(?:is|are)\s+(?:looking|hiring|seeking|searching)|"
    r"\b(?:hiring|recruiting|looking|searching)\s+for\s+(?:a|an|one\s+of\s+our|our)\s+(?:leading|reputed|top|well[- ]known|renowned|"
    r"multinational|fortune|esteemed|mnc)\b|"
    r"\bc2h\b|contract[- ]to[- ]hire|\bthird[- ]party\s+payroll\b|payroll\s+of\s+(?:the\s+)?(?:company|client|consultancy)|"
    r"\bwe\s+are\s+(?:a|an)\s+(?:[\w-]+\s+){0,2}(?:staffing|recruit\w*)\s+(?:agency|firm|company|consultancy|partner)\b",
    re.I,
)


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def agency_reason(company: str, description: str = "") -> str:
    """Why this looks like an agency ('' = it does not). Used for the tooltip on the Staffing tag."""
    if _NAME.search(company or ""):
        return "The company name says it is a staffing or recruiting firm"
    key = _key(company)
    if key in KNOWN or any(key.startswith(k) for k in KNOWN if len(k) >= 7):          # "TeamLease Digital" -> teamlease
        return "A well-known placement firm or talent marketplace"
    if _TEXT.search((description or "")[:2500]):
        return "The posting says it is hiring on behalf of a client"
    return ""


def is_agency(company: str, description: str = "") -> bool:
    return bool(agency_reason(company, description))
