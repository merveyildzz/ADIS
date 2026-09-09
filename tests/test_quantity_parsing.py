import pytest

from app.common.quantity_parsing import looks_like_quantity, normalize_numeric_literal, parse_quantity


def test_dollar_with_million_magnitude():
    m = parse_quantity("$1.2M")
    assert m.amount == pytest.approx(1_200_000.0)
    assert m.currency_code == "USD"
    assert m.magnitude_code == "M"
    assert m.unit is None
    assert m.confidence_hint == "high"


def test_rupee_with_lacs_magnitude():
    m = parse_quantity("₹5 Lacs")
    assert m.amount == pytest.approx(500_000.0)
    assert m.currency_code == "INR"
    assert m.magnitude_code == "Lacs"


def test_rupee_with_crore_magnitude():
    m = parse_quantity("₹8.5 Cr")
    assert m.amount == pytest.approx(85_000_000.0)
    assert m.magnitude_code == "Cr"


def test_rupee_with_single_letter_l_lakh_abbreviation():
    # The single-letter "L" abbreviation (vs. spelled-out "Lacs"/"Lakh") is
    # extremely common in real Indian real-estate listings — found missing
    # when a real Kaggle-style dataset's Flat_Price column (56% "L"-suffixed
    # values) scored just under the classification threshold without it.
    m = parse_quantity("₹1.35 L")
    assert m.amount == pytest.approx(135_000.0)
    assert m.currency_code == "INR"
    assert m.magnitude_code == "L"


def test_percent_unit_with_no_currency():
    m = parse_quantity("50%")
    assert m.amount == pytest.approx(50.0)
    assert m.currency_code is None
    assert m.unit == "percent"


def test_combined_currency_magnitude_and_unit():
    m = parse_quantity("₹20.24 K/sq.ft")
    assert m.amount == pytest.approx(20240.0)
    assert m.currency_code == "INR"
    assert m.magnitude_code == "K"
    assert m.unit == "sqft"


def test_plain_sqft_number_with_no_currency():
    m = parse_quantity("1500 sq.ft")
    assert m.amount == pytest.approx(1500.0)
    assert m.currency_code is None
    assert m.unit == "sqft"


def test_ambiguous_mixed_separator_locale_drops_confidence_not_a_silent_misparse():
    # A mixed dot-then-comma literal ("1.234,56" — European dot-thousands +
    # comma-decimal) matches neither of this codebase's unambiguous shapes
    # (plain decimal, comma-decimal, dot-thousands) — must not be silently
    # misparsed; it's still parsed best-effort but flagged lower-confidence.
    value, was_ambiguous = normalize_numeric_literal("1.234,56")
    assert was_ambiguous is True
    assert value == pytest.approx(1234.56)

    m = parse_quantity("$1.234,56")
    assert m is not None
    assert m.confidence_hint == "medium"


def test_no_match_returns_none():
    assert parse_quantity("not a number at all") is None
    assert parse_quantity("") is None


def test_looks_like_quantity_requires_currency_magnitude_or_unit_evidence():
    # A bare plain number has no currency/magnitude/unit evidence of its
    # own — it must not trivially count as "quantity" (that would let any
    # numeric ID column falsely qualify at the column-detection layer).
    assert looks_like_quantity("42") is False
    assert looks_like_quantity("$42") is True
    assert looks_like_quantity("42 Cr") is True
    assert looks_like_quantity("42%") is True
