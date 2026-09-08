"""Column-type inference: column name heuristics + regex/pattern sampling of
values — no LLM call. Deliberately weighted so that *value* evidence, not
the header name, decides the routing: a column named like a date but full
of free text must not get routed to the Date Agent (see the "random notes"
adversarial test in the roadmap).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from app.common.turkey_geo import ALL_PROVINCES, COUNTRY

SAMPLE_SIZE = 50

# A type is only assignable if its value-match rate alone clears this floor —
# the header-keyword bonus can never make up the difference on its own. This
# is what stops a column named "order_date" full of free text from being
# force-routed to the Date Agent.
MIN_VALUE_EVIDENCE = 0.30
# The combined (value + header bonus) score needed to assign any agent at
# all; short of this the column is left "unclassified".
MIN_TOTAL_CONFIDENCE = 0.50
HEADER_BONUS = 0.15

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_DATE_PATTERNS = [
    re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$"),
    re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$"),
    re.compile(r"^\d{1,2}-\d{1,2}-\d{4}$"),
    re.compile(r"^[A-Za-z]+ \d{1,2},\s?\d{4}$"),
]

_CURRENCY_PATTERNS = [
    re.compile(r"^\$\s?\d[\d,]*\.?\d*$"),          # $120.50
    re.compile(r"^\d[\d.]*\s?TL$"),                 # 1.500 TL
    re.compile(r"^₺\s?\d[\d,]*$"),                   # ₺2000
    re.compile(r"^\d+,\d{1,2}$"),                    # 85,50 (bare comma-decimal)
]

NUMERIC_REGEX = re.compile(r"^-?\d+(\.\d+)?$")

_PROVINCES_LOWER = [p.lower() for p in ALL_PROVINCES] + [COUNTRY.lower()]

HEADER_KEYWORDS: dict[str, set[str]] = {
    "email": {"email", "mail", "e-mail"},
    "phone": {"phone", "tel", "mobile", "gsm"},
    "date": {"date", "dt"},
    "currency": {"amount", "price", "cost", "total", "currency", "fee", "fiyat", "tutar"},
    "address": {"address", "addr", "city", "location", "adres"},
    "numeric_age": {"age", "yas", "yaş"},
}

# For "numeric" specifically, a bare digit string is too weak a signal on
# its own (an ID, a count, a price with the symbol stripped...) — a header
# hint is *required*, not just a bonus, to offer it as a candidate at all.
TYPES_REQUIRING_HEADER_HINT = {"numeric_age"}

TYPE_TO_AGENT: dict[str, str] = {
    "email": "ContactAgent",
    "phone": "ContactAgent",
    "date": "DateAgent",
    "currency": "CurrencyAgent",
    "address": "AddressAgent",
    "numeric_age": "NumericAgent",
}


def _is_date_like(value: str) -> bool:
    return any(p.match(value) for p in _DATE_PATTERNS)


def _is_currency_like(value: str) -> bool:
    return any(p.match(value) for p in _CURRENCY_PATTERNS)


def _is_phone_like(value: str) -> bool:
    compact = re.sub(r"[\s\-()]", "", value)
    return bool(re.match(r"^\+?\d{9,13}$", compact))


def _is_address_like(value: str) -> bool:
    lowered = value.lower()
    has_known_place = any(p in lowered for p in _PROVINCES_LOWER)
    has_multi_part = ("," in value) or (" - " in value)
    return has_known_place and has_multi_part


_VALUE_MATCHERS = {
    "email": lambda v: bool(EMAIL_REGEX.match(v)),
    "phone": _is_phone_like,
    "date": _is_date_like,
    "currency": _is_currency_like,
    "address": _is_address_like,
    "numeric_age": lambda v: bool(NUMERIC_REGEX.match(v)),
}


@dataclass
class ColumnTypeResult:
    column_name: str
    detected_type: str | None  # None => unclassified
    agent_type: str  # "unclassified" when detected_type is None
    confidence: float  # 0-1, the winning type's total score (0 if unclassified)
    sample_size: int
    scores: dict[str, float] = field(default_factory=dict)


def _sample_values(series: pd.Series, sample_size: int) -> list[str]:
    non_null = series.dropna().astype(str)
    non_null = non_null[non_null.str.strip() != ""]
    return non_null.head(sample_size).tolist()


def detect_column_type(series: pd.Series, column_name: str, sample_size: int = SAMPLE_SIZE) -> ColumnTypeResult:
    samples = _sample_values(series, sample_size)

    if not samples:
        return ColumnTypeResult(
            column_name=column_name, detected_type=None, agent_type="unclassified",
            confidence=0.0, sample_size=0, scores={},
        )

    header_lower = column_name.lower()
    scores: dict[str, float] = {}

    for type_name, matcher in _VALUE_MATCHERS.items():
        if type_name in TYPES_REQUIRING_HEADER_HINT:
            header_hits = any(kw in header_lower for kw in HEADER_KEYWORDS[type_name])
            if not header_hits:
                continue  # not even a candidate without the header hint

        value_match_rate = sum(1 for v in samples if matcher(v)) / len(samples)
        if value_match_rate < MIN_VALUE_EVIDENCE:
            continue  # header bonus alone can never carry a type past this point

        header_hits = any(kw in header_lower for kw in HEADER_KEYWORDS.get(type_name, set()))
        bonus = HEADER_BONUS if header_hits else 0.0
        scores[type_name] = min(1.0, value_match_rate + bonus)

    if not scores:
        return ColumnTypeResult(
            column_name=column_name, detected_type=None, agent_type="unclassified",
            confidence=0.0, sample_size=len(samples), scores={},
        )

    best_type, best_score = max(scores.items(), key=lambda kv: kv[1])
    if best_score < MIN_TOTAL_CONFIDENCE:
        return ColumnTypeResult(
            column_name=column_name, detected_type=None, agent_type="unclassified",
            confidence=0.0, sample_size=len(samples), scores=scores,
        )

    return ColumnTypeResult(
        column_name=column_name,
        detected_type=best_type,
        agent_type=TYPE_TO_AGENT[best_type],
        confidence=best_score,
        sample_size=len(samples),
        scores=scores,
    )
