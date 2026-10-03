"""Small text helpers: HTML -> text, normalisation, date parsing, location tags."""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Optional

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr)[^>]*>", re.I)
_WS_RE = re.compile(r"[ \t\r\f\v\u00a0]+")
_NL_RE = re.compile(r"\n\s*\n+")


def html_to_text(raw: str | None, limit: int | None = None) -> str:
    """Convert (possibly entity-escaped) HTML to tidy plain text."""
    if not raw:
        return ""
    text = raw
    # Greenhouse double-escapes its HTML (&lt;div&gt;...); unescape until stable (max 2 passes).
    for _ in range(2):
        if "&lt;" in text or "&amp;" in text or "&#" in text:
            text = html.unescape(text)
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _NL_RE.sub("\n\n", text).strip()
    if limit and len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def norm(s: str | None) -> str:
    """Lowercase, strip punctuation and collapse whitespace (for fingerprints)."""
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def to_iso(value) -> Optional[str]:
    """Best-effort conversion of ISO strings / epoch seconds / epoch millis to UTC ISO-8601."""
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            num = float(value)
            if num > 1e11:  # milliseconds
                num /= 1000.0
            return datetime.fromtimestamp(num, tz=timezone.utc).isoformat(timespec="seconds")
        s = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, OverflowError, OSError):
        return None


INDIA_TOKENS = (
    "india", "bangalore", "bengaluru", "hyderabad", "pune", "mumbai", "delhi", "gurgaon", "gurugram",
    "noida", "chennai", "kolkata", "ahmedabad", "chandigarh", "jaipur", "kochi", "cochin", "indore",
    "coimbatore", "thiruvananthapuram", "trivandrum", "nagpur", "lucknow", "mohali", "bhubaneswar",
    "vadodara", "surat", "ncr", "mysuru", "mysore",
)
_INDIA_RE = re.compile(r"\b(" + "|".join(INDIA_TOKENS) + r")\b", re.I)
_REMOTE_RE = re.compile(r"\b(remote|anywhere|work from home|wfh|distributed)\b", re.I)


# ISO country code as used by YC ("Pune, MH, IN / Mumbai, MH, IN") - case-sensitive on purpose.
_INDIA_CODE = re.compile(r"(?:,\s*|/\s*)IN(?=\s*(?:/|,|$))")


def is_india(location: str) -> bool:
    loc = location or ""
    return bool(_INDIA_RE.search(loc) or _INDIA_CODE.search(loc))


def looks_remote(location: str) -> bool:
    return bool(_REMOTE_RE.search(location or ""))


_GLOBAL_RE = re.compile(r"\b(worldwide|anywhere|everywhere|global|globally|apac|asia)\b", re.I)
_FILLER_RE = re.compile(r"\b(remote|hybrid|wfh|work from home|distributed|fully|only|first|friendly|flexible)\b", re.I)


def remote_eligible(location: str, flag: bool = False) -> bool:
    """True when a remote job can plausibly be taken from India.

    "Remote", "Remote - India", "Anywhere", "Remote (APAC)" -> True
    "Remote - US", "Europe" (remote flag) -> False (geo-restricted)
    """
    loc = location or ""
    if not (flag or looks_remote(loc)):
        return False
    if is_india(loc) or _GLOBAL_RE.search(loc):
        return True
    rest = re.sub(r"[^a-z]", "", _FILLER_RE.sub(" ", loc.lower()))
    return len(rest) < 2
