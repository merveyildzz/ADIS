"""Shared building blocks every cleaning agent uses: a common result shape,
a decorator that guarantees a single bad row can never crash a batch, and a
helper to write every decision to the Phase 0 audit trail.

Confidence bands (roadmap Phase 4): High 90-100 (single unambiguous
interpretation), Medium 60-89 (inferred from column-level context/majority
pattern), Low <60 (genuinely ambiguous or the agent had to guess).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("agents")

CONFIDENCE_HIGH = 95.0
CONFIDENCE_MEDIUM = 75.0
CONFIDENCE_LOW = 35.0
CONFIDENCE_NONE = 0.0


@dataclass
class AgentResult:
    original_value: Any
    cleaned_value: str | None
    confidence: float
    method: str
    agent_type: str
    flagged: bool = False  # could not be confidently cleaned / failed a final schema check
    details: dict = field(default_factory=dict)


def safe_clean_row(agent_type: str) -> Callable:
    """A per-row cleaner must never raise — one malformed row must not stop
    the whole batch. Any unhandled exception is caught here, logged, and
    turned into a flagged, zero-confidence result instead."""

    def decorator(fn: Callable[..., AgentResult]) -> Callable[..., AgentResult]:
        def wrapper(original_value: Any, *args: Any, **kwargs: Any) -> AgentResult:
            try:
                return fn(original_value, *args, **kwargs)
            except Exception:
                logger.exception("%s failed to clean value %r", agent_type, original_value)
                return AgentResult(
                    original_value=original_value,
                    cleaned_value=None,
                    confidence=CONFIDENCE_NONE,
                    method="error_unprocessed",
                    agent_type=agent_type,
                    flagged=True,
                    details={"error": "unhandled_exception_during_cleaning"},
                )

        return wrapper

    return decorator


def normalize_for_feedback_lookup(value: str) -> str:
    """"Near-identical" (Phase 6) is defined as equal after trimming and
    collapsing whitespace and case — not full fuzzy matching. Cheap,
    deterministic, and covers the realistic near-duplicate case (the same
    raw cell value reappearing with different padding/casing)."""
    return " ".join(value.strip().lower().split())


def build_feedback_map(corrections) -> dict[str, str]:
    """Turns a list of FeedbackCorrection rows into the normalized-value ->
    corrected-value lookup agents check first. Corrections are passed in
    most-recent-first, so `dict()` naturally keeps the latest one on key
    collision without needing an explicit sort here."""
    feedback_map: dict[str, str] = {}
    for correction in reversed(list(corrections)):
        feedback_map[normalize_for_feedback_lookup(correction.original_value)] = correction.corrected_value
    return feedback_map


def check_feedback(value: str, feedback_map: dict[str, str] | None, agent_type: str) -> AgentResult | None:
    """Zero-LLM-cost reuse: if this (near-identical) value was corrected by
    a user before, use that correction directly instead of re-running the
    normal cleaning logic. Returns None (defer to normal cleaning) when
    there's no match — including when `feedback_map` is empty or None,
    which is exactly the "feedback table is empty" fallback case."""
    if not feedback_map:
        return None
    corrected = feedback_map.get(normalize_for_feedback_lookup(value))
    if corrected is None:
        return None
    return AgentResult(
        original_value=value,
        cleaned_value=corrected,
        confidence=CONFIDENCE_HIGH,
        method="reused_prior_feedback",
        agent_type=agent_type,
        details={"influenced_by_prior_feedback": True},
    )


def log_column_cleaning(
    *, decision_logger, agent_type: str, column_name: str, results: list[AgentResult], upload_id: int | None
) -> None:
    for r in results:
        decision_logger.log(
            agent_name=agent_type,
            action="clean_value",
            input_value=r.original_value,
            output_value=r.cleaned_value,
            confidence=r.confidence,
            upload_id=upload_id,
            details={"method": r.method, "flagged": r.flagged, "column": column_name, **r.details},
        )
