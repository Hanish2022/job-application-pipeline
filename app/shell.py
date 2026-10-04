"""Server-rendered app shell: the brand + navigation shared by the Jobs page and the Cold-email page.

Only static markup lives here (no user data), so there is nothing to escape. Styling is in app/static/shell.css.
"""
from __future__ import annotations

from html import escape

from . import config

_ICONS = {
    "briefcase": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="7" width="18" height="13" rx="2.5"/><path d="M8.5 7V5.5A1.5 1.5 0 0 1 10 4h4a1.5 1.5 0 0 1 1.5 1.5V7M3 13h18"/></svg>',
    "mail": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M3.5 7.5 12 13l8.5-5.5"/></svg>',
    "refresh": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 11a8 8 0 0 0-14.3-4.5L4 8.5M4 4v4.5h4.5M4 13a8 8 0 0 0 14.3 4.5L20 15.5M20 20v-4.5h-4.5"/></svg>',
    "shield": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 3 5 6v5.5c0 4.3 2.9 7.6 7 9.5 4.1-1.9 7-5.2 7-9.5V6l-7-3z"/><path d="m9 12 2.2 2.2L15.5 10"/></svg>',
    "sliders": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true" focusable="false"><path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/></svg>',
}
ICONS = _ICONS

_NAV_ITEMS = (("jobs", "/", "Jobs", "briefcase"), ("outreach", "/outreach", "Cold email", "mail"))

_RIGHT = {
    "jobs": (
        '<div class="topnav__right">'
        '<div class="fresh" id="run-meta" data-state="never" aria-live="polite" data-testid="run-meta"></div>'
        '<button class="btn btn--secondary btn--filters-toggle" id="filters-toggle" type="button" aria-expanded="false" aria-controls="filters">'
        f'{_ICONS["sliders"]}<span class="btn__text">Filters</span></button>'
        '<button class="btn btn--secondary btn--profile" id="open-profile" type="button" data-testid="open-profile">'
        '<span class="avatar" id="avatar" aria-hidden="true">·</span><span class="btn__text">Profile</span></button>'
        '<button class="btn btn--primary btn--refresh" id="refresh" type="button" data-testid="refresh">'
        f'{_ICONS["refresh"]}<span class="btn__label">Refresh jobs</span></button>'
        "</div>"
    ),
    "outreach": (
        '<div class="topnav__right">'
        f'<span class="note-chip" data-testid="safety-note">{_ICONS["shield"]}Nothing is sent automatically</span>'
        "</div>"
    ),
}


def wordmark(name: str) -> str:
    """Two-tone wordmark: 'Acme Jobs' -> Acme <span>Jobs</span>; a one-word name stays fully bold. Escaped."""
    first, _, rest = name.strip().partition(" ")
    return escape(first) + (f" <span>{escape(rest)}</span>" if rest else "")


def nav_html(active: str) -> str:
    """The full <header>. `active` is "jobs" or "outreach"."""
    if active not in _RIGHT:
        raise ValueError(f"unknown page: {active}")
    links = ""
    for key, href, label, icon in _NAV_ITEMS:
        current = ' aria-current="page"' if key == active else ""
        links += f'<a class="navlink" href="{href}"{current}>{_ICONS[icon]}<span>{label}</span></a>'
    return (
        '<header class="topnav" data-ui-build="__UI_BUILD__"><div class="topnav__inner">'
        f'<a class="brand" href="/" aria-label="{escape(config.APP_NAME)} home" title="{escape(config.APP_NAME)} · UI build __UI_BUILD__">'
        f'<span class="brand__mark" aria-hidden="true">{_ICONS["briefcase"]}</span>'
        f'<span class="brand__name">{wordmark(config.APP_NAME)}</span></a>'
        f'<nav class="topnav__nav" aria-label="Primary" data-testid="nav">{links}</nav>'
        f"{_RIGHT[active]}"
        "</div></header>"
    )
