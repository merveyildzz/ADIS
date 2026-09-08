"""Numeric Agent — no LLM. Handles the numeric-but-messy columns the four
named agents don't cover (in our dataset: `customer_age`), including the
exact case from the Phase 5 lineage example: `" thirty-five "` -> `35`.
"""
from __future__ import annotations

import re

import pandas as pd

from app.agents.base import CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, AgentResult, check_feedback, safe_clean_row

AGENT_TYPE = "NumericAgent"

_PLAUSIBLE_AGE_RANGE = (0, 120)

_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}


def _words_to_number(text: str) -> int | None:
    words = re.split(r"[\s-]+", text.strip().lower())
    words = [w for w in words if w]
    if not words:
        return None
    if len(words) == 1 and words[0] in _ONES:
        return _ONES[words[0]]
    if len(words) == 1 and words[0] in _TENS:
        return _TENS[words[0]]
    if len(words) == 2 and words[0] in _TENS and words[1] in _ONES and _ONES[words[1]] < 10:
        return _TENS[words[0]] + _ONES[words[1]]
    return None


@safe_clean_row(AGENT_TYPE)
def _clean_one(raw_value: str) -> AgentResult:
    value = raw_value.strip()

    if re.match(r"^-?\d+$", value):
        n = int(value)
        method = "rule_based_normalization" if value == raw_value else "whitespace_stripped"
        confidence = CONFIDENCE_HIGH if value == raw_value else CONFIDENCE_HIGH
        if not (_PLAUSIBLE_AGE_RANGE[0] <= n <= _PLAUSIBLE_AGE_RANGE[1]):
            return AgentResult(
                value, str(n), CONFIDENCE_MEDIUM, "implausible_range", AGENT_TYPE, flagged=True,
                details={"plausible_range": _PLAUSIBLE_AGE_RANGE},
            )
        return AgentResult(value, str(n), confidence, method, AGENT_TYPE)

    n = _words_to_number(value)
    if n is not None:
        # Matches the Phase 5 lineage example exactly: " thirty-five " -> 35, 98%.
        return AgentResult(value, str(n), 98.0, "text_to_number_pattern", AGENT_TYPE)

    return AgentResult(value, None, 0.0, "unparseable", AGENT_TYPE, flagged=True)


def clean_column(series: pd.Series, feedback_map: dict[str, str] | None = None) -> list[AgentResult]:
    results: list[AgentResult] = []
    for v in series.tolist():
        if pd.isna(v) or str(v).strip() == "":
            results.append(AgentResult(v, None, 0.0, "missing_value", AGENT_TYPE, flagged=True))
            continue
        feedback_result = check_feedback(str(v), feedback_map, AGENT_TYPE)
        if feedback_result is not None:
            results.append(feedback_result)
            continue
        results.append(_clean_one(str(v)))
    return results
