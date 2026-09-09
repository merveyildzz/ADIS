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

from app.common.quantity_parsing import looks_like_quantity
from app.common.turkey_geo import ALL_PROVINCES, COUNTRY

# Sample up to this many non-null values per column. The "~15-20 values"
# content-based-profiling target is already satisfied by MIN_VALUE_EVIDENCE
# (30% of up to 50 samples ~= 15) — this constant is intentionally left
# unchanged so every existing confidence threshold/test keeps its current
# denominator; shrinking it would silently shift borderline scores.
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

# A "categorical" column is a profiling classification, not a cleaning
# target (no agent owns it) — but detecting it lets the Orchestrator skip an
# unnecessary LLM classification call for an already-understood bounded
# enum, and (via the ratio bound) stops a high-cardinality free-text column
# from ever being mistaken for one.
MAX_CATEGORICAL_UNIQUE_COUNT = 50
MAX_CATEGORICAL_UNIQUE_RATIO = 0.2

# Header keywords that specifically suggest a physical/percentage dimension
# rather than money — used only to let a column of otherwise-bare numbers
# (no currency symbol, no magnitude suffix in any cell) qualify as
# "quantity" evidence, e.g. a `Total_Sq.ft` column holding plain integers.
# This never applies to money-suggesting keywords like "price"/"amount":
# a bare number under one of those is still genuinely ambiguous (could be
# an ID, a count, anything) and is deliberately left for the LLM-fallback /
# profiling path rather than force-classified on name alone.
_QUANTITY_DIMENSIONAL_HEADER_KEYWORDS = {"sq.ft", "sqft", "sq ft", "area", "size", "%", "percent"}

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
    "quantity": {"price", "amount", "sq.ft", "sqft", "area", "size", "emi", "%", "percent"},
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
    "quantity": "QuantityAgent",
    # Categorical is a profiling classification, never a cleaning target.
    "categorical": "unclassified",
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
    # Inserted right after "currency" so that on an exact score tie between
    # the two (both clear MIN_TOTAL_CONFIDENCE equally), Python's max() keeps
    # the first-encountered entry — biasing toward the older, narrower
    # CurrencyAgent detector and keeping the original synthetic dataset's
    # `order_amount` routing unchanged after this detector set was broadened.
    "quantity": looks_like_quantity,
    "address": _is_address_like,
    "numeric_age": lambda v: bool(NUMERIC_REGEX.match(v)),
}


@dataclass
class ColumnProfile:
    """Populated whenever a column ends up unclassified (whether no
    detector matched, or the LLM classification fallback also returned
    "unknown") — the "profiled but not transformed" surface: a column no
    agent could confidently clean is still reported in useful detail,
    never silently left as a bare label."""

    null_pct: float
    unique_count: int
    inferred_dtype: str
    min_value: str | None
    max_value: str | None
    llm_attempted: bool = False
    llm_agent_guess: str | None = None


@dataclass
class ColumnTypeResult:
    column_name: str
    detected_type: str | None  # None => unclassified
    agent_type: str  # "unclassified" when detected_type is None
    confidence: float  # 0-1, the winning type's total score (0 if unclassified)
    sample_size: int
    scores: dict[str, float] = field(default_factory=dict)
    profile: ColumnProfile | None = None


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

        if type_name == "quantity":
            # A bare number (no currency symbol/magnitude/unit in the value
            # itself) only counts as "quantity" evidence when the header
            # names an actual physical dimension (e.g. `Total_Sq.ft`) — the
            # column still has to BE mostly numeric; the header only
            # disambiguates what a plain number means, it never substitutes
            # for value evidence on its own.
            dimensional_header_hit = any(kw in header_lower for kw in _QUANTITY_DIMENSIONAL_HEADER_KEYWORDS)
            value_match_rate = sum(
                1 for v in samples if matcher(v) or (dimensional_header_hit and NUMERIC_REGEX.match(v))
            ) / len(samples)
        else:
            value_match_rate = sum(1 for v in samples if matcher(v)) / len(samples)
        if value_match_rate < MIN_VALUE_EVIDENCE:
            continue  # header bonus alone can never carry a type past this point

        header_hits = any(kw in header_lower for kw in HEADER_KEYWORDS.get(type_name, set()))
        bonus = HEADER_BONUS if header_hits else 0.0
        scores[type_name] = min(1.0, value_match_rate + bonus)

    if not scores or max(scores.values()) < MIN_TOTAL_CONFIDENCE:
        categorical_score = _categorical_score(samples)
        if categorical_score is not None:
            return ColumnTypeResult(
                column_name=column_name, detected_type="categorical", agent_type="unclassified",
                confidence=categorical_score, sample_size=len(samples), scores=scores,
            )
        return ColumnTypeResult(
            column_name=column_name, detected_type=None, agent_type="unclassified",
            confidence=0.0, sample_size=len(samples), scores=scores,
        )

    best_type, best_score = max(scores.items(), key=lambda kv: kv[1])

    return ColumnTypeResult(
        column_name=column_name,
        detected_type=best_type,
        agent_type=TYPE_TO_AGENT[best_type],
        confidence=best_score,
        sample_size=len(samples),
        scores=scores,
    )


def _categorical_score(samples: list[str]) -> float | None:
    """A bounded-cardinality signal, not a per-value pattern — cardinality
    is a whole-column property, so this doesn't fit the per-value
    `_VALUE_MATCHERS` shape and is checked separately, only once no
    cleaning-agent type has already won. The ratio bound is what stops a
    high-cardinality free-text column (e.g. 3000 distinct values) from ever
    being mistaken for a clean, bounded enum."""
    unique_count = len(set(samples))
    if 2 <= unique_count <= MAX_CATEGORICAL_UNIQUE_COUNT and unique_count / len(samples) <= MAX_CATEGORICAL_UNIQUE_RATIO:
        return round(1.0 - (unique_count / len(samples)), 4)
    return None


def _is_orderable(series: pd.Series) -> bool:
    try:
        series.min()
        series.max()
        return True
    except TypeError:
        return False


def build_column_profile(series: pd.Series) -> ColumnProfile:
    """The "profiled but not transformed" report for a column no detector
    (and no LLM fallback) could confidently classify — null%, unique count,
    inferred dtype, and min/max where orderable. Never raises: a column of
    mixed/incomparable free text simply gets null min/max instead of
    crashing, so even an all-unclassifiable dataset still yields a full,
    valid report."""
    total = len(series)
    non_null = series.dropna()
    orderable = _is_orderable(non_null) and not non_null.empty
    return ColumnProfile(
        null_pct=round(100 * (1 - len(non_null) / total), 2) if total else 0.0,
        unique_count=int(non_null.nunique()),
        inferred_dtype=str(series.dtype),
        min_value=str(non_null.min()) if orderable else None,
        max_value=str(non_null.max()) if orderable else None,
    )
