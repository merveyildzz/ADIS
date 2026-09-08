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
