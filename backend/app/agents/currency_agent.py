"""Currency Agent — no LLM. Detects currency symbol/code via regex,
normalizes the number to a plain decimal string, and reports the detected
currency code separately (`details["currency_code"]`).

Decimal-separator rule (fixed, stated up front — not column-majority, since
each of our four display styles is unambiguous by its own shape):
a comma followed by exactly 1-2 digits is a decimal separator; a dot used
as a thousands grouping (exactly 3 digits per group, no trailing decimal
part) is stripped. This matches the "$120.50" / "1.500 TL" / "85,50" /
"₺2000" styles from Phase 1 exactly.
"""
from __future__ import annotations

import re

import pandas as pd

from app.agents.base import CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, AgentResult, check_feedback, safe_clean_row

AGENT_TYPE = "CurrencyAgent"

_USD_RE = re.compile(r"^\s*\$\s?(\d[\d,]*\.?\d*)\s*$")
_TL_SUFFIX_RE = re.compile(r"^\s*(\d[\d.]*)\s?TL\s*$")
_TRY_SYMBOL_RE = re.compile(r"^\s*₺\s?(\d[\d,]*)\s*$")
_BARE_COMMA_RE = re.compile(r"^\s*(\d+),(\d{1,2})\s*$")
_PLAIN_NUMBER_RE = re.compile(r"^\s*(\d+(\.\d+)?)\s*$")

# Assumed for values with no symbol/code at all and no comma-decimal
# evidence — a bare integer in this Turkish-business-context dataset.
# Stated explicitly here rather than left implicit.
DEFAULT_ASSUMED_CURRENCY = "TRY"


@safe_clean_row(AGENT_TYPE)
def _clean_one(raw_value: str, currency_hint: str | None) -> AgentResult:
    value = raw_value.strip()

    m = _USD_RE.match(value)
    if m:
        amount = float(m.group(1).replace(",", ""))
        return AgentResult(value, f"{amount:.2f}", CONFIDENCE_HIGH, "symbol_usd",
                            AGENT_TYPE, details={"currency_code": "USD"})

    m = _TL_SUFFIX_RE.match(value)
    if m:
        amount = float(m.group(1).replace(".", ""))  # dot = thousands grouping here
        return AgentResult(value, f"{amount:.2f}", CONFIDENCE_HIGH, "suffix_tl",
                            AGENT_TYPE, details={"currency_code": "TRY"})

    m = _TRY_SYMBOL_RE.match(value)
    if m:
        amount = float(m.group(1).replace(",", ""))
        return AgentResult(value, f"{amount:.2f}", CONFIDENCE_HIGH, "symbol_try",
                            AGENT_TYPE, details={"currency_code": "TRY"})

    m = _BARE_COMMA_RE.match(value)
    if m:
        amount = float(f"{m.group(1)}.{m.group(2)}")
        code = (currency_hint or DEFAULT_ASSUMED_CURRENCY).strip().upper() or DEFAULT_ASSUMED_CURRENCY
        return AgentResult(
            value, f"{amount:.2f}", CONFIDENCE_MEDIUM, "bare_comma_decimal_currency_assumed",
            AGENT_TYPE, details={"currency_code": code, "currency_assumed": True},
        )

    m = _PLAIN_NUMBER_RE.match(value)
    if m:
        amount = float(m.group(1))
        code = (currency_hint or DEFAULT_ASSUMED_CURRENCY).strip().upper() or DEFAULT_ASSUMED_CURRENCY
        return AgentResult(
            value, f"{amount:.2f}", CONFIDENCE_MEDIUM, "plain_number_currency_assumed",
            AGENT_TYPE, details={"currency_code": code, "currency_assumed": True},
        )

    return AgentResult(value, None, 0.0, "unparseable", AGENT_TYPE, flagged=True)


def clean_column(
    series: pd.Series, currency_hints: pd.Series | None = None, feedback_map: dict[str, str] | None = None
) -> list[AgentResult]:
    results: list[AgentResult] = []
    for i, v in enumerate(series.tolist()):
        if pd.isna(v) or str(v).strip() == "":
            results.append(AgentResult(v, None, 0.0, "missing_value", AGENT_TYPE, flagged=True))
            continue
        feedback_result = check_feedback(str(v), feedback_map, AGENT_TYPE)
        if feedback_result is not None:
            results.append(feedback_result)
            continue
        hint = None
        if currency_hints is not None:
            h = currency_hints.iloc[i]
            hint = None if pd.isna(h) else str(h)
        results.append(_clean_one(str(v), hint))
    return results
