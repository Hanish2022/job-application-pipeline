"""Salary extraction + normalisation to LPA (lakhs per annum, INR).

Sources express pay in many ways ("INR 1,800,000–2,200,000 annual", "$150K – $200K • Offers Equity",
"₹12–18 LPA", a `salaryRange` object …).  Everything is converted to a (min_lpa, max_lpa) pair so it
can be shown on a card and filtered on.

Foreign currencies are converted with the fixed approximate rates in FX_TO_INR — good enough for
"is this roughly 20 LPA or 60 LPA", not for payroll.  Converted figures are shown with a "≈".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Approximate INR per 1 unit of currency. Edit if you want tighter numbers.
FX_TO_INR: dict[str, float] = {
    "INR": 1.0, "USD": 88.0, "EUR": 100.0, "GBP": 115.0, "CAD": 64.0, "AUD": 58.0,
    "SGD": 68.0, "AED": 24.0, "CHF": 105.0,
}
PERIOD_TO_YEAR = {"year": 1.0, "month": 12.0, "week": 52.0, "day": 260.0, "hour": 2080.0}

_SYMBOL = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}
_CODES = "usd|eur|gbp|inr|cad|aud|sgd|aed|chf"
_NUM = r"\d[\d,]*(?:\.\d+)?"

# A sane annual range in INR lakhs. Anything outside is almost certainly not a salary.
MIN_SANE_LPA, MAX_SANE_LPA = 0.1, 1000.0   # 0.1 LPA = ₹10k/yr: keeps intern stipends, drops '$5 - $10'

_PAY_CONTEXT = re.compile(
    r"salary|compensation|\bpay\b|base|ctc|lpa|stipend|per annum|annual|per year|/\s*yr|/\s*year|range|wage|remuneration|\bote\b",
    re.I,
)

# 1) Indian style: "12-18 LPA", "₹ 12 to 18 lakhs", "8 LPA", "1.2 Cr"
_LPA = re.compile(
    rf"(?:₹|rs\.?|inr)?\s*(?P<a>\d+(?:\.\d+)?)\s*(?:lpa|lakhs?|lacs?|crores?|cr\b)?\s*(?:(?:-|–|—|to)\s*(?:₹|rs\.?|inr)?\s*(?P<b>\d+(?:\.\d+)?))?\s*"
    r"(?P<unit>lpa|lakhs?(?:\s+per\s+annum|\s+p\.?a\.?)?|lacs?|l\.?p\.?a\.?|crores?|cr\b)",
    re.I,
)

# 2) Currency range: "$150K – $200K", "USD 1,800,000-2,200,000", "€96,000 - €130,000", "₹25,000 - 30,000 per month"
_RANGE = re.compile(
    rf"(?P<c1>[$€£₹]|\b(?:{_CODES})\b|\brs\.?)?\s*(?P<a>{_NUM})\s*(?P<ak>k|m|l|lpa|lakhs?|lacs?|cr)?(?![a-z])\s*(?P<c2>\b(?:{_CODES})\b)?\s*"
    rf"(?:-|–|—|to)\s*(?P<c3>[$€£₹]|\b(?:{_CODES})\b|\brs\.?)?\s*(?P<b>{_NUM})\s*(?P<bk>k|m|l|lpa|lakhs?|lacs?|cr)?(?![a-z])\s*(?P<c4>\b(?:{_CODES})\b)?",
    re.I,
)
_PERIOD_RE = [
    ("hour", re.compile(r"per\s+hour|/\s*h(?:ou)?r\b|hourly|an\s+hour", re.I)),
    ("month", re.compile(r"per\s+month|/\s*mo(?:nth)?\b|monthly|a\s+month|\bp\.?m\.?\b", re.I)),
    ("week", re.compile(r"per\s+week|/\s*w(?:ee)?k\b|weekly", re.I)),
    ("day", re.compile(r"per\s+day|/\s*day\b|daily", re.I)),
    ("year", re.compile(r"per\s+(?:year|annum)|/\s*y(?:ea)?r\b|annual|yearly|p\.?a\.?\b", re.I)),
]


@dataclass
class Salary:
    min_lpa: float
    max_lpa: float
    currency: str          # original currency ("INR" => no conversion happened)
    raw: str               # the text it was parsed from

    @property
    def converted(self) -> bool:
        return self.currency != "INR"


def to_lpa(amount: float, currency: str, period: str = "year") -> Optional[float]:
    rate = FX_TO_INR.get((currency or "").upper())
    mult = PERIOD_TO_YEAR.get(period or "year")
    if rate is None or mult is None or amount is None:
        return None
    return amount * mult * rate / 100_000.0


def from_structured(lo: float | None, hi: float | None, currency: str, period: str = "year", raw: str = "") -> Optional[Salary]:
    """Build a Salary from numeric fields a source gave us directly."""
    lo = float(lo) if lo else None
    hi = float(hi) if hi else None
    if not lo and not hi:
        return None
    lo, hi = lo or hi, hi or lo
    a, b = to_lpa(lo, currency, period), to_lpa(hi, currency, period)
    return _finish(a, b, (currency or "").upper(), raw)


def _finish(a: Optional[float], b: Optional[float], currency: str, raw: str) -> Optional[Salary]:
    if a is None or b is None:
        return None
    a, b = sorted((a, b))
    if not (MIN_SANE_LPA <= a and b <= MAX_SANE_LPA):
        return None
    return Salary(round(a, 2), round(b, 2), currency, raw.strip())


def _currency(*cands: str | None) -> str:
    for c in cands:
        if not c:
            continue
        c = c.strip()
        if c in _SYMBOL:
            return _SYMBOL[c]
        c = c.upper().rstrip(".")
        if c == "RS":
            return "INR"
        if c in FX_TO_INR:
            return c
    return ""


def _period_near(text: str, end: int) -> str:
    window = text[end : end + 40]
    for name, rx in _PERIOD_RE:
        if rx.search(window):
            return name
    return "year"


_INR_ONLY_SUFFIX = {"l", "lpa", "lakh", "lakhs", "lac", "lacs", "cr"}


def _suffix_mult(s: str | None) -> float:
    s = (s or "").lower()
    if s in _INR_ONLY_SUFFIX:
        return 1e7 if s == "cr" else 1e5
    return {"k": 1e3, "m": 1e6}.get(s, 1.0)


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def parse_text(text: str, require_context: bool = False) -> Optional[Salary]:
    """Find the first plausible pay range in free text.

    require_context: for long job descriptions, only accept a match that has pay-related words nearby
    (avoids things like "layers 1–7" or "raised $50M–$100M")."""
    if not text:
        return None

    for m in _LPA.finditer(text):
        a = _num(m.group("a"))
        b = _num(m.group("b")) if m.group("b") else a
        unit = m.group("unit").lower()
        if unit.startswith("cr"):
            a, b = a * 100, b * 100
        if require_context and not _PAY_CONTEXT.search(text[max(0, m.start() - 120) : m.end() + 120]) and "lpa" not in unit and "lakh" not in unit:
            continue
        s = _finish(a, b, "INR", m.group(0))
        if s:
            return s

    for m in _RANGE.finditer(text):
        cur = _currency(m.group("c1"), m.group("c2"), m.group("c3"), m.group("c4"))
        if not cur:
            continue   # a bare "1–7" is not money
        suffixes = {(m.group("ak") or "").lower(), (m.group("bk") or "").lower()}
        if suffixes & _INR_ONLY_SUFFIX and cur != "INR":
            continue   # "L"/"lakh"/"Cr" only make sense for rupees
        a = _num(m.group("a")) * _suffix_mult(m.group("ak") or m.group("bk"))
        b = _num(m.group("b")) * _suffix_mult(m.group("bk") or m.group("ak"))
        if require_context and not _PAY_CONTEXT.search(text[max(0, m.start() - 150) : m.end() + 150]):
            continue
        period = _period_near(text, m.end())
        s = _finish(to_lpa(a, cur, period), to_lpa(b, cur, period), cur, m.group(0))
        if s:
            return s
    return None


def extract(salary_text: str = "", description: str = "", structured: Optional[Salary] = None) -> Optional[Salary]:
    """Best available salary: structured source data > the source's salary string > pay text in the description."""
    return structured or parse_text(salary_text or "") or parse_text(description or "", require_context=True)


def _fmt(n: float) -> str:
    n = round(n, 1)
    return str(int(n)) if n == int(n) else f"{n:.1f}"


def format_lpa(lo: Optional[float], hi: Optional[float], converted: bool = False) -> str:
    if lo is None or hi is None:
        return ""
    body = f"₹{_fmt(lo)} LPA" if _fmt(lo) == _fmt(hi) else f"₹{_fmt(lo)}–{_fmt(hi)} LPA"
    return ("≈ " if converted else "") + body
