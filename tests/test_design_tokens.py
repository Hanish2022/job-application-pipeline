"""The colour system is checked against the CSS itself: change a token and these tests tell you if it stopped being readable."""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
SHELL = (STATIC / "shell.css").read_text()


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def tokens():
    hexes = dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;", SHELL))
    rgbs = {k: tuple(int(v) for v in vals.split(",")) for k, vals in re.findall(r"--([a-z]+)-rgb:\s*([\d,\s]+);", SHELL)}
    return hexes, rgbs


HEXES, RGBS = tokens()
SURFACES = {name: hex_rgb(HEXES[name]) for name in ("canvas", "surface-1", "surface-2", "surface-3")}


def lum(rgb):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def ratio(a, b):
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def over(fg, alpha, bg):
    return tuple(round(alpha * f + (1 - alpha) * b) for f, b in zip(fg, bg))


def test_tokens_were_parsed():
    assert {"blue", "green", "yellow", "red", "violet", "orange", "pink", "teal", "gray"} <= set(RGBS)
    assert {"canvas", "surface-1", "surface-2", "ink", "ink-muted", "ink-subtle", "primary"} <= set(HEXES)


@pytest.mark.parametrize("colour", ["blue", "green", "yellow", "red", "violet", "orange", "pink", "teal", "gray"])
@pytest.mark.parametrize("surface", list(SURFACES))
def test_tag_text_is_readable_on_its_own_15_percent_tint(colour, surface):
    fg = RGBS[colour]
    bg = over(fg, 0.15, SURFACES[surface])
    assert ratio(fg, bg) >= 4.5, f"{colour} tag text on {surface}: {ratio(fg, bg):.2f}:1"


@pytest.mark.parametrize("colour", ["blue", "green", "yellow", "red", "violet", "orange", "pink", "teal", "gray"])
def test_rings_dots_and_borders_meet_the_3_to_1_non_text_minimum(colour):
    assert ratio(RGBS[colour], SURFACES["surface-1"]) >= 3.0


@pytest.mark.parametrize("name", ["ink", "ink-muted", "ink-subtle"])
@pytest.mark.parametrize("surface", ["canvas", "surface-1", "surface-2"])
def test_body_text_tokens_meet_aa(name, surface):
    assert ratio(hex_rgb(HEXES[name]), SURFACES[surface]) >= 4.5


def test_primary_button_text_and_the_brand_violet_rules():
    white = (255, 255, 255)
    assert ratio(white, hex_rgb(HEXES["primary"])) >= 4.5                         # white label on the primary button
    # The brand violet itself must NOT be used for small text on dark surfaces (it fails); the lighter violet tag colour is.
    assert ratio(hex_rgb(HEXES["primary"]), SURFACES["surface-1"]) < 4.5
    css = "".join((STATIC / f).read_text() for f in ("shell.css", "styles.css", "outreach.css"))
    offenders = re.findall(r"[^-]color:\s*var\(--primary\)", css)
    assert not offenders, "brand violet used as a text colour"


def test_every_semantic_class_is_defined():
    for c in ("blue", "green", "yellow", "red", "violet", "orange", "pink", "teal", "gray"):
        assert re.search(rf"\.c-{c}\s*{{\s*--c:\s*var\(--{c}-rgb\)", SHELL), c


def test_colour_meaning_is_consistent_across_the_app():
    """One colour = one meaning. Guards the mapping used by the Jobs page and the Cold-email page."""
    js = (STATIC / "app.js").read_text()
    assert re.search(r'LEVEL_COLOR = \{ intern: "violet", entry: "green", mid: "yellow", senior: "red", unknown: "gray" \}', js)
    assert 'n >= 80 ? "green" : n >= 60 ? "blue" : n >= 40 ? "yellow" : "gray"' in js
    cold = (STATIC / "outreach.css").read_text()
    assert re.search(r"\.badge\.site \{ --c: var\(--blue-rgb\)", cold) and re.search(r"\.badge\.valid \{ --c: var\(--green-rgb\)", cold)
    assert re.search(r"\.badge\.guess, \.badge\.unknown \{ --c: var\(--yellow-rgb\)", cold)


def test_mobile_navigation_dock_does_not_rely_on_a_containing_filter():
    """A backdrop-filter on the header would make the fixed bottom dock position itself relative to the header (a classic bug)."""
    m = re.search(r"\.topnav \{([^}]*)\}", SHELL)
    assert m and "backdrop-filter" not in m.group(1)


def test_the_old_product_name_is_gone_from_the_app():
    """The product is renamed in one place (config.APP_NAME); no stale copies of the old name remain in code or markup."""
    root = STATIC.parent
    stale = []
    for path in list(root.glob("*.py")) + list(STATIC.glob("*")):
        if path.is_file() and path.suffix in {".py", ".html", ".js", ".css"}:
            text = path.read_text()
            for needle in ("Job Pipeline", "Job <span>Pipeline"):
                if needle in text:
                    stale.append(f"{path.name}: {needle}")
    assert stale == [], stale
