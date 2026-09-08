"""Structured logging + the agent-decision audit trail.

Every agent decision (input, output, confidence, timestamp) is recorded here.
This module defines the recording interface now; Phase 2 adds a database-backed
sink (writing into the `audit_log` table) that plugs into the same interface
without requiring any agent call sites to change.
"""
from __future__ import annotations

import json
import logging
import logging.config
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"


def setup_logging() -> None:
    """Configure console + rotating-file logging for the whole app."""
    settings = get_settings()
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "level": settings.log_level,
                },
                "file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "formatter": "default",
                    "filename": str(LOG_DIR / "app.log"),
                    "maxBytes": 5 * 1024 * 1024,
                    "backupCount": 3,
                    "level": settings.log_level,
                },
            },
            "root": {
                "handlers": ["console", "file"],
                "level": settings.log_level,
            },
        }
    )


@dataclass(frozen=True)
class AgentDecision:
    """One agent decision — the unit of the audit trail."""

    agent_name: str
    action: str
    input_value: Any
    output_value: Any
    confidence: Optional[float] = None
    upload_id: Optional[int] = None
    details: Optional[dict] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


class DecisionSink(ABC):
    """A destination for agent decisions. Phase 2 implements a DB-backed
    sink against the `audit_log` table; this file only needs the interface."""

    @abstractmethod
    def write(self, decision: AgentDecision) -> None:
        raise NotImplementedError


class JsonlFileSink(DecisionSink):
    """Default sink: append-only JSON-lines file. Guarantees the audit trail
    is captured even before/without a database sink being wired up."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or (LOG_DIR / "agent_decisions.jsonl")
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, decision: AgentDecision) -> None:
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(decision.to_dict(), default=str) + "\n")


class AgentDecisionLogger:
    """Fan-out recorder for agent decisions. Agents call `log(...)` once;
    every registered sink (file today, DB from Phase 2 onward) receives it.
    A failure in one sink must never crash the pipeline or take others down.
    """

    def __init__(self, sinks: Optional[list[DecisionSink]] = None) -> None:
        self._sinks = sinks if sinks is not None else [JsonlFileSink()]
        self._logger = logging.getLogger("audit")

    def add_sink(self, sink: DecisionSink) -> None:
        self._sinks.append(sink)

    def log(
        self,
        *,
        agent_name: str,
        action: str,
        input_value: Any,
        output_value: Any,
        confidence: Optional[float] = None,
        upload_id: Optional[int] = None,
        details: Optional[dict] = None,
    ) -> None:
        decision = AgentDecision(
            agent_name=agent_name,
            action=action,
            input_value=input_value,
            output_value=output_value,
            confidence=confidence,
            upload_id=upload_id,
            details=details,
        )
        for sink in self._sinks:
            try:
                sink.write(decision)
            except Exception:
                self._logger.exception(
                    "Failed to write agent decision to sink %s", type(sink).__name__
                )


_decision_logger: Optional[AgentDecisionLogger] = None


def get_decision_logger() -> AgentDecisionLogger:
    global _decision_logger
    if _decision_logger is None:
        _decision_logger = AgentDecisionLogger()
    return _decision_logger
