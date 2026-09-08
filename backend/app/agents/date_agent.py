"""Date Agent — no LLM. Detects format via regex + `dateutil.parser`,
normalizes to ISO 8601. Day/month order is inferred from the majority
pattern in the rest of the column when a single row can't settle it alone;
if the column offers no majority evidence either, we still produce a
best-effort value but flag it and score it Low — never a silent guess.
"""
from __future__ import annotations

import re
from datetime import date

import pandas as pd
from dateutil import parser as dateutil_parser

from app.agents.base import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    AgentResult,
    check_feedback,
    safe_clean_row,
)

AGENT_TYPE = "DateAgent"

_ISO_RE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$")
_NUMERIC_SEP_RE = re.compile(r"^\s*(\d{1,2})[/-](\d{1,2})[/-](\d{4})\s*$")
_TEXTUAL_RE = re.compile(r"^\s*[A-Za-z]+ \d{1,2},?\s*\d{4}\s*$")


def _infer_column_dayfirst(values: list[str]) -> tuple[bool | None, int]:
    """Scans numeric-separator dates where one component is unambiguously >12
    (so it must be the day) and tallies whether that unambiguous day sits in
    the first or second position — the column's majority convention.
    Returns (dayfirst, evidence_count); dayfirst is None with no evidence."""
    first_is_day_votes = 0
    second_is_day_votes = 0
    for v in values:
        m = _NUMERIC_SEP_RE.match(v)
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12 and b <= 12:
            first_is_day_votes += 1
        elif b > 12 and a <= 12:
            second_is_day_votes += 1
    total = first_is_day_votes + second_is_day_votes
    if total == 0:
        return None, 0
    return first_is_day_votes >= second_is_day_votes, total


@safe_clean_row(AGENT_TYPE)
def _clean_one(raw_value: str, dayfirst_hint: bool | None, evidence_count: int) -> AgentResult:
    value = raw_value.strip()

    iso_match = _ISO_RE.match(value)
    if iso_match:
        y, m, d = (int(g) for g in iso_match.groups())
        parsed = date(y, m, d)
        return AgentResult(value, parsed.isoformat(), CONFIDENCE_HIGH, "iso_parse", AGENT_TYPE)

    if _TEXTUAL_RE.match(value):
        parsed = dateutil_parser.parse(value).date()
        return AgentResult(value, parsed.isoformat(), CONFIDENCE_HIGH, "textual_month_parse", AGENT_TYPE)

    m = _NUMERIC_SEP_RE.match(value)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 12 and b <= 12:
            parsed = date(y, b, a)  # a must be the day
            return AgentResult(value, parsed.isoformat(), CONFIDENCE_HIGH, "unambiguous_day_gt_12", AGENT_TYPE)
        if b > 12 and a <= 12:
            parsed = date(y, a, b)  # b must be the day
            return AgentResult(value, parsed.isoformat(), CONFIDENCE_HIGH, "unambiguous_day_gt_12", AGENT_TYPE)
        if a > 12 and b > 12:
            return AgentResult(value, None, 0.0, "invalid_date_components", AGENT_TYPE, flagged=True)

        # Both components <=12: genuinely ambiguous from this row alone.
        if dayfirst_hint is not None and evidence_count >= 3:
            day, month = (a, b) if dayfirst_hint else (b, a)
            parsed = date(y, month, day)
            return AgentResult(
                value, parsed.isoformat(), CONFIDENCE_MEDIUM, "majority_pattern_inference", AGENT_TYPE,
                details={"column_dayfirst_evidence_count": evidence_count},
            )

        # No reliable column-level signal either — still produce a best
        # guess (documented fixed default: day-first) but score it Low and
        # flag it, so this is visibly a guess, not silently trusted.
        day, month = a, b
        try:
            parsed = date(y, month, day)
            return AgentResult(
                value, parsed.isoformat(), CONFIDENCE_LOW, "ambiguous_guessed_low_confidence", AGENT_TYPE,
                flagged=True,
            )
        except ValueError:
            return AgentResult(value, None, 0.0, "invalid_date_components", AGENT_TYPE, flagged=True)

    return AgentResult(value, None, 0.0, "unparseable", AGENT_TYPE, flagged=True)


def clean_column(series: pd.Series, feedback_map: dict[str, str] | None = None) -> list[AgentResult]:
    raw_values = series.astype(object).tolist()
    string_values = [str(v).strip() for v in raw_values if pd.notna(v) and str(v).strip() != ""]
    dayfirst_hint, evidence_count = _infer_column_dayfirst(string_values)

    results: list[AgentResult] = []
    for v in raw_values:
        if pd.isna(v) or str(v).strip() == "":
            results.append(AgentResult(v, None, 0.0, "missing_value", AGENT_TYPE, flagged=True))
            continue
        feedback_result = check_feedback(str(v), feedback_map, AGENT_TYPE)
        if feedback_result is not None:
            results.append(feedback_result)
            continue
        results.append(_clean_one(str(v), dayfirst_hint, evidence_count))
    return results
