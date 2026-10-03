import pytest

from app.salary import Salary, extract, format_lpa, from_structured, parse_text, to_lpa


def lpa(text, **kw):
    s = parse_text(text, **kw)
    return (s.min_lpa, s.max_lpa, s.currency) if s else None


@pytest.mark.parametrize("text,expected", [
    ("₹12-18 LPA", (12, 18, "INR")),
    ("12 to 18 LPA", (12, 18, "INR")),
    ("Rs. 8 to 12 lakhs per annum", (8, 12, "INR")),
    ("10 LPA", (10, 10, "INR")),
    ("₹ 6.5 - 9 Lacs", (6.5, 9, "INR")),
    ("INR 1,800,000–2,200,000 annual", (18, 22, "INR")),
    ("INR 600,000–800,000 annual", (6, 8, "INR")),
    ("₹25,000 - 30,000 per month", (3, 3.6, "INR")),        # stipends: monthly -> annual
    ("$80k - $150k", (70.4, 132, "USD")),
    ("$210.7K – $316.1K • Offers Equity • Offers Bonus", (185.42, 278.21, "USD")),
    ("$244,000 — $305,000 USD", (214.72, 268.4, "USD")),
    ("EUR 35,000–50,000 annual", (35, 50, "EUR")),
    ("€96,000 - €130,000", (96, 130, "EUR")),
    ("USD 35–35 hourly", (64.06, 64.06, "USD")),            # hourly x 2080h
])
def test_parses_common_formats(text, expected):
    got = lpa(text)
    assert got is not None, text
    assert got[2] == expected[2]
    assert got[0] == pytest.approx(expected[0], abs=0.05) and got[1] == pytest.approx(expected[1], abs=0.05)


@pytest.mark.parametrize("text", [
    "", "OSI Model layers 1–7", "We raised $50M-$100M", "2023 - 2027", "3-5 years of experience", "Grade 7-9",
    "Join us in 2-3 weeks", "$5 - $10 million", "₹1 - 2",
])
def test_rejects_things_that_are_not_salaries(text):
    assert parse_text(text) is None


def test_description_needs_pay_context():
    with_ctx = "Benefits included. Estimated annual salary of $361,000-$496,000 for Bay Area hires."
    assert lpa(with_ctx, require_context=True)[2] == "USD"
    assert parse_text("Our team lunches cost $20-30 and we love them", require_context=True) is None
    assert parse_text("Visit us: rooms EUR 100-200 nightly", require_context=True) is None


def test_extract_precedence_structured_then_salary_text_then_description():
    structured = from_structured(10, 20, "INR", "year")       # 0.1-0.2 LPA -> below the sanity floor
    assert structured is None
    good = from_structured(1_000_000, 1_500_000, "INR", "year")
    assert extract("$1-2", "", structured=good) is good
    assert extract("₹12-18 LPA", "salary $100,000-$120,000").min_lpa == 12
    assert extract("", "The salary range is ₹20-25 LPA.").max_lpa == 25
    assert extract("", "nothing here") is None


def test_from_structured_conversions():
    s = from_structured(60000, 80000, "USD", "year")
    assert (s.min_lpa, s.max_lpa) == (52.8, 70.4) and s.converted
    m = from_structured(50000, 60000, "INR", "month")
    assert (m.min_lpa, m.max_lpa) == (6.0, 7.2) and not m.converted
    assert from_structured(None, None, "USD") is None
    assert from_structured(100, 200, "XYZ") is None            # unknown currency
    one = from_structured(1_200_000, None, "INR")
    assert one.min_lpa == one.max_lpa == 12
    swapped = from_structured(2_000_000, 1_000_000, "INR")
    assert (swapped.min_lpa, swapped.max_lpa) == (10, 20)


def test_sanity_bounds_drop_absurd_values():
    assert from_structured(1, 2, "USD", "year") is None                 # ~0.0002 LPA
    assert from_structured(10_000_000, 20_000_000, "USD", "year") is None   # tens of thousands of LPA


def test_to_lpa():
    assert to_lpa(100_000, "USD") == pytest.approx(88.0)
    assert to_lpa(1_200_000, "INR") == 12
    assert to_lpa(10, "USD", "hour") == pytest.approx(18.304)
    assert to_lpa(10, "ZZZ") is None and to_lpa(10, "USD", "fortnight") is None


def test_format_lpa():
    assert format_lpa(12, 18) == "₹12–18 LPA"
    assert format_lpa(12, 12) == "₹12 LPA"
    assert format_lpa(6.5, 9) == "₹6.5–9 LPA"
    assert format_lpa(70.4, 132, converted=True) == "≈ ₹70.4–132 LPA"
    assert format_lpa(None, None) == ""
    assert isinstance(Salary(1, 2, "INR", "").converted, bool)


@pytest.mark.parametrize("text,expected", [
    ("₹1.2L – ₹1.8L", (1.2, 1.8)), ("₹1.2L – ₹1.8L • No equity", (1.2, 1.8)), ("₹20–35 lakh/year (based on experience)", (20, 35)),
    ("₹2.5M - ₹5M INR", (25, 50)), ("₹42,000 – ₹42,000", (0.42, 0.42)), ("₹2 Cr - 3 Cr", (200, 300)),
])
def test_indian_short_forms(text, expected):
    got = lpa(text)
    assert got and got[2] == "INR" and got[0] == pytest.approx(expected[0], abs=0.01) and got[1] == pytest.approx(expected[1], abs=0.01)


@pytest.mark.parametrize("text", ["$5L - $6L", "₹3,000 – ₹5,000", "$25 - $40", "₹1 - 2"])
def test_implausible_or_mixed_forms_are_rejected(text):
    assert parse_text(text) is None
