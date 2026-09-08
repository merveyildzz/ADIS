"""Narrative Agent — the only LLM use in the Insight layer, and it never
computes anything. It receives *already-computed* statistics and phrases
them in plain language; the deterministic agents (Correlation/Trend/
Anomaly) own every number. Never given raw data, never given DB access.

Every correlation-based narrative gets the causation disclaimer appended by
this code, unconditionally — not something the LLM is trusted to remember,
and not conditioned on whether the LLM's own phrasing already implied it.

If the LLM is unavailable, fails, or its output mentions a number that
doesn't trace back to the given statistics (a hallucination guard, not
just a style check), this falls back to a template sentence — which also
carries the disclaimer. Every code path produces a working narrative.
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel

from app.insights.anomaly_agent import AnomalyInsight
from app.insights.correlation_agent import CorrelationInsight
from app.insights.trend_agent import TrendInsight
from app.llm.client import LLMClient

logger = logging.getLogger("insights.narrative")

CAUSATION_DISCLAIMER = "Correlation does not imply causation."

_SYSTEM_PROMPT = (
    "You turn already-computed statistics into one clear, plain-language "
    "sentence for a business audience. The user message is a JSON object "
    "of exact statistics that have already been calculated — you perform "
    "NO calculation of your own and introduce NO number that is not "
    "already present in that JSON. Do not round differently, do not "
    "estimate, do not add context numbers you were not given. One or two "
    "sentences, no caveats or disclaimers (those are added separately)."
)


class NarrativeText(BaseModel):
    text: str


def _extract_numbers(text: str) -> list[float]:
    return [float(m) for m in re.findall(r"-?\d+\.?\d*", text)]


def _allowed_numbers(stats: dict) -> set[float]:
    """Every number in `stats`, plus its common rephrasings (as a percentage,
    rounded to whole/1dp) — an LLM legitimately writing "71%" for a given
    r=0.71 shouldn't be flagged as hallucinating."""
    allowed: set[float] = set()
    for value in stats.values():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        for candidate in (value, value * 100, abs(value), abs(value) * 100):
            allowed.add(round(float(candidate), 3))
            allowed.add(round(float(candidate), 1))
            allowed.add(round(float(candidate), 0))
    return allowed


def _no_hallucinated_numbers(text: str, stats: dict, tolerance: float = 0.05) -> bool:
    allowed = _allowed_numbers(stats)
    for n in _extract_numbers(text):
        if not any(abs(n - a) <= tolerance for a in allowed):
            return False
    return True


def _generate(stats: dict, llm_client: LLMClient | None, template: str) -> tuple[str, str]:
    """Returns (text, method). Tries the LLM if available; on any failure,
    unavailability, or a hallucinated number, falls back to `template`."""
    if llm_client is None:
        return template, "template_fallback_no_llm"

    result = llm_client.extract_structured(
        system_prompt=_SYSTEM_PROMPT, data=stats, response_model=NarrativeText
    )
    if result is None:
        return template, "template_fallback_llm_unavailable"
    if not _no_hallucinated_numbers(result.text, stats):
        logger.warning("Narrative Agent output contained an unverifiable number; using template instead.")
        return template, "template_fallback_hallucination_guard"
    return result.text, "llm_generated"


def narrate_correlation(insight: CorrelationInsight, llm_client: LLMClient | None = None) -> dict:
    verb = "increase" if insight.direction == "positive" else "decrease"
    template = f"As {insight.col1} increases, {insight.col2} tends to {verb} (r={insight.r})."
    stats = {"metric": "correlation", "col1": insight.col1, "col2": insight.col2,
              "r": insight.r, "direction": insight.direction, "strength": insight.strength}
    text, method = _generate(stats, llm_client, template)
    # Unconditional: every correlation narrative carries this, regardless
    # of source or phrasing — a rule the code enforces, not the model.
    return {"text": f"{text} {CAUSATION_DISCLAIMER}", "method": method}


def narrate_trend(insight: TrendInsight, llm_client: LLMClient | None = None) -> dict:
    verb = "increased" if insight.direction == "increase" else "decreased"
    if insight.dimension == "numeric_sum":
        template = f"{insight.label} {verb} {abs(insight.change_pct)}% in {insight.period}."
    else:
        template = (
            f"{insight.label} orders {verb} {abs(insight.change_pct)}% in {insight.period} "
            "compared to the average month."
        )
    stats = {"metric": "trend", "label": insight.label, "period": insight.period,
              "baseline": insight.baseline, "period_value": insight.period_value,
              "change_pct": insight.change_pct, "direction": insight.direction}
    text, method = _generate(stats, llm_client, template)
    return {"text": text, "method": method}


def narrate_anomaly(insight: AnomalyInsight, llm_client: LLMClient | None = None) -> dict:
    template = (
        f"Row {insight.row_index}'s {insight.column} value of {insight.value} is "
        f"{abs(insight.z_score)}σ {insight.direction} the normal range."
    )
    stats = {"metric": "anomaly", "column": insight.column, "value": insight.value,
              "z_score": insight.z_score, "direction": insight.direction}
    text, method = _generate(stats, llm_client, template)
    return {"text": text, "method": method}
