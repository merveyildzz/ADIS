"""Generalized currency/quantity parsing — the broader sibling of
`currency_agent.py`'s four fixed TL/USD shapes. Recognizes *any* of a small
fixed set of currency symbols plus a number, and *separately* recognizes
magnitude suffixes (K, Lac(s), Cr(ore), M/Mn) and non-monetary unit suffixes
(sq.ft, %) — normalizing everything into a plain float amount plus separate
currency/magnitude/unit metadata fields, the same normalize-then-annotate
contract `currency_agent.py` already uses for TL/USD.

Decimal-separator handling reuses the same fixed, stated-up-front rule as
`currency_agent.py`: a comma followed by exactly 1-2 digits is a decimal
separator; a dot used as thousands grouping (exactly 3-digit groups, no
trailing decimal part) is stripped. A literal that matches neither shape
cleanly (an unfamiliar locale/separator convention) is still parsed on a
best-effort basis but flagged `was_ambiguous=True` so callers can lower
confidence instead of silently misparsing it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CURRENCY_SYMBOLS: dict[str, str] = {
    "$": "USD",
    "€": "EUR",
    "₺": "TRY",
    "₹": "INR",
    "£": "GBP",
}
_CURRENCY_CODES = {"USD", "EUR", "TRY", "INR", "GBP"}

# Multiplier applied to the numeric mantissa when this suffix is present.
# "l" is the single-letter Lakh abbreviation extremely common in Indian
# real-estate listings (e.g. "₹1.35 L") alongside the spelled-out
# "Lacs"/"Lakh(s)" forms — found missing when a real Kaggle-style
# house-price dataset's Flat_Price column (56% "L"-suffixed values) scored
# just under the classification threshold without it.
MAGNITUDE_MULTIPLIERS: dict[str, float] = {
    "k": 1_000,
    "l": 100_000,
    "lac": 100_000,
    "lacs": 100_000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "cr": 10_000_000,
    "crore": 10_000_000,
    "m": 1_000_000,
    "mn": 1_000_000,
}

# Non-monetary unit suffixes — multiplier 1, just annotated in `unit`.
UNIT_SUFFIXES: dict[str, str] = {
    "sq.ft": "sqft",
    "sqft": "sqft",
    "sq ft": "sqft",
    "%": "percent",
}

_CURRENCY_SYMBOL_CLASS = "".join(re.escape(s) for s in CURRENCY_SYMBOLS)
_CURRENCY_CODE_ALT = "|".join(sorted(_CURRENCY_CODES, key=len, reverse=True))
_MAGNITUDE_ALT = "|".join(sorted(MAGNITUDE_MULTIPLIERS, key=len, reverse=True))
_UNIT_ALT = "|".join(re.escape(u) for u in sorted(UNIT_SUFFIXES, key=len, reverse=True))

_QUANTITY_RE = re.compile(
    rf"^\s*(?P<currency>[{_CURRENCY_SYMBOL_CLASS}]|{_CURRENCY_CODE_ALT})?\s*"
    rf"(?P<number>\d[\d,.]*)\s*"
    rf"(?P<magnitude>{_MAGNITUDE_ALT})?\s*"
    rf"(?:/\s*)?(?P<unit>{_UNIT_ALT})?\s*$",
    re.IGNORECASE,
)

_COMMA_DECIMAL_RE = re.compile(r"^\d+,\d{1,2}$")
_DOT_THOUSANDS_RE = re.compile(r"^\d{1,3}(\.\d{3})+$")
_PLAIN_RE = re.compile(r"^\d+(\.\d+)?$")


@dataclass(frozen=True)
class QuantityMatch:
    amount: float
    currency_code: str | None
    magnitude_code: str | None
    unit: str | None
    confidence_hint: str  # "high" | "medium" — "medium" when the separator convention was ambiguous


def normalize_numeric_literal(raw: str) -> tuple[float, bool]:
    """Returns (value, was_ambiguous). `was_ambiguous=True` means the
    literal didn't cleanly match either the comma-decimal or dot-thousands
    shape this codebase treats as unambiguous — it's still parsed
    best-effort, but callers should not treat the result as high-confidence."""
    stripped = raw.replace(" ", "")
    if _PLAIN_RE.match(stripped):
        return float(stripped), False
    if _COMMA_DECIMAL_RE.match(stripped):
        return float(stripped.replace(",", ".")), False
    if _DOT_THOUSANDS_RE.match(stripped):
        return float(stripped.replace(".", "")), False

    # Ambiguous shape (e.g. mixed "1.234,56", or a lone comma not matching
    # the 1-2-digit decimal pattern). Best-effort: strip whichever separator
    # appears to be thousands-grouping and treat the other as decimal.
    has_comma, has_dot = "," in stripped, "." in stripped
    try:
        if has_comma and has_dot:
            if stripped.rfind(",") > stripped.rfind("."):
                value = float(stripped.replace(".", "").replace(",", "."))
            else:
                value = float(stripped.replace(",", ""))
        elif has_comma:
            value = float(stripped.replace(",", ""))
        else:
            value = float(stripped)
        return value, True
    except ValueError:
        raise


def parse_quantity(raw: str) -> QuantityMatch | None:
    match = _QUANTITY_RE.match(raw.strip())
    if not match:
        return None
    number_literal = match.group("number")
    if not re.search(r"\d", number_literal):
        return None

    try:
        amount, was_ambiguous = normalize_numeric_literal(number_literal)
    except ValueError:
        return None

    currency_raw = match.group("currency")
    currency_code = None
    if currency_raw:
        currency_code = CURRENCY_SYMBOLS.get(currency_raw, currency_raw.upper())

    magnitude_raw = match.group("magnitude")
    magnitude_code = None
    if magnitude_raw:
        magnitude_code = magnitude_raw
        amount *= MAGNITUDE_MULTIPLIERS[magnitude_raw.lower()]

    unit_raw = match.group("unit")
    unit = UNIT_SUFFIXES.get(unit_raw.lower()) if unit_raw else None

    return QuantityMatch(
        amount=amount,
        currency_code=currency_code,
        magnitude_code=magnitude_code,
        unit=unit,
        confidence_hint="medium" if was_ambiguous else "high",
    )


def looks_like_quantity(raw: str) -> bool:
    """Cheap boolean wrapper for column_detection's value-matcher slot —
    requires at least one of currency/magnitude/unit evidence so a bare
    plain integer (already covered by other detectors) doesn't trivially
    qualify as "quantity" on number-shape alone."""
    match = parse_quantity(raw)
    if match is None:
        return False
    return bool(match.currency_code or match.magnitude_code or match.unit)
