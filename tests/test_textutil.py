from app.textutil import html_to_text, is_india, looks_remote, norm, remote_eligible, to_iso


def test_html_to_text_handles_greenhouse_double_escaping():
    raw = "&lt;div&gt;&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;&lt;ul&gt;&lt;li&gt;React&lt;/li&gt;&lt;li&gt;Node&lt;/li&gt;&lt;/ul&gt;&lt;/div&gt;"
    text = html_to_text(raw)
    assert "<" not in text and ">" not in text
    assert "Hello & welcome" in text
    assert "React" in text and "Node" in text


def test_html_to_text_plain_and_empty_and_limit():
    assert html_to_text(None) == ""
    assert html_to_text("") == ""
    assert html_to_text("<b>Bold</b> text") == "Bold text"
    long = html_to_text("<p>" + "word " * 1000 + "</p>", limit=50)
    assert len(long) <= 51 and long.endswith("…")


def test_html_to_text_strips_script_like_markup_but_keeps_text():
    assert "alert" in html_to_text("<p>alert(1)</p>")
    assert "<script" not in html_to_text("<script>x</script>hi")


def test_norm():
    assert norm("  Software   Engineer – II! ") == "software engineer ii"
    assert norm(None) == ""


def test_to_iso_variants():
    assert to_iso("2026-10-01T10:00:00-04:00") == "2026-10-01T14:00:00+00:00"
    assert to_iso("2026-10-01T10:00:00Z") == "2026-10-01T10:00:00+00:00"
    assert to_iso(1791009036) == "2026-10-03T06:30:36+00:00"
    assert to_iso("1791009036000") == "2026-10-03T06:30:36+00:00"  # millis
    assert to_iso("2026-10-01T10:00:00").endswith("+00:00")        # naive -> UTC
    assert to_iso(None) is None and to_iso("garbage") is None and to_iso("") is None


def test_is_india():
    for loc in ["Bengaluru, India", "IN-Pune", "Remote - India", "Gurugram", "Hyderabad, Telangana", "Mumbai"]:
        assert is_india(loc), loc
    for loc in ["San Francisco", "Indianapolis", "London", ""]:
        assert not is_india(loc), loc


def test_looks_remote():
    assert looks_remote("Remote - US") and looks_remote("Work from home") and not looks_remote("Pune")


def test_remote_eligible_from_india():
    assert remote_eligible("Remote")
    assert remote_eligible("Remote - India")
    assert remote_eligible("Remote – Worldwide")
    assert remote_eligible("Remote (APAC)")
    assert remote_eligible("", flag=True)
    assert not remote_eligible("Remote - US")
    assert not remote_eligible("Remote – United States, Canada")
    assert not remote_eligible("Europe", flag=True)
    assert not remote_eligible("Berlin, Germany")
    assert not remote_eligible("Pune")


def test_is_india_handles_yc_country_codes():
    assert is_india("Bengaluru, KA, IN / Pune, MH, IN")
    assert is_india("Remote, IN")
    for loc in ["Austin, TX, US / Seattle, WA, US", "Berlin, DE", "Lagos, NG", "INDIANA"]:
        assert not is_india(loc), loc


def test_remote_everywhere_is_eligible():
    assert remote_eligible("Remote ( . Everywhere. )") or remote_eligible("Remote – Everywhere")
