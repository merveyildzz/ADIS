"""Quantity Agent — no LLM. The generalized sibling of CurrencyAgent: parses
*any* recognized currency symbol plus a number, and/or a magnitude suffix
(K, Lac(s), Cr(ore), M/Mn) and/or a non-monetary unit suffix (sq.ft, %),
normalizing the number (applying the magnitude multiplier) and reporting
currency/magnitude/unit separately in `details` — the same
normalize-then-annotate contract `currency_agent.py` uses for TL/USD.

Kept as a separate agent (not folded into CurrencyAgent) because a unit-only
value like `1450 sq.ft` isn't currency at all — labeling it `CurrencyAgent`
would be semantically wrong and would corrupt anything that assumes
`agent_type == "CurrencyAgent"` implies money (e.g. revenue trend charts).
"""
from __future__ import annotations

import pandas as pd

from app.agents.base import CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, AgentResult, check_feedback, safe_clean_row
from app.common.quantity_parsing import parse_quantity

AGENT_TYPE = "QuantityAgent"


@safe_clean_row(AGENT_TYPE)
def _clean_one(raw_value: str) -> AgentResult:
    value = raw_value.strip()
    match = parse_quantity(value)
    if match is None:
        return AgentResult(value, None, 0.0, "unparseable", AGENT_TYPE, flagged=True)

    confidence = CONFIDENCE_HIGH if match.confidence_hint == "high" else CONFIDENCE_MEDIUM
    return AgentResult(
        value, f"{match.amount:.2f}", confidence, "quantity_normalized", AGENT_TYPE,
        details={
            "currency_code": match.currency_code,
            "magnitude": match.magnitude_code,
            "unit": match.unit,
        },
    )


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
