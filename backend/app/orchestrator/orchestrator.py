"""Ties file validation + column-type detection into one entry point: hand
it raw upload bytes, get back a validated DataFrame and a routing plan of
{column_name: agent_type} for the Phase 4 cleaning agents to consume.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from app.config import get_settings
from app.core.logging_config import get_decision_logger
from app.orchestrator.column_detection import ColumnTypeResult, detect_column_type
from app.orchestrator.file_validation import ValidatedUpload, validate_and_load_upload


@dataclass
class RoutingPlan:
    column_agents: dict[str, str]  # column_name -> agent_type ("unclassified" if none matched)
    column_results: dict[str, ColumnTypeResult]
    warnings: list[str] = field(default_factory=list)


def build_routing_plan(df: pd.DataFrame, *, upload_id: int | None = None) -> RoutingPlan:
    column_agents: dict[str, str] = {}
    column_results: dict[str, ColumnTypeResult] = {}
    decision_logger = get_decision_logger()

    for column_name in df.columns:
        result = detect_column_type(df[column_name], column_name)
        column_agents[column_name] = result.agent_type
        column_results[column_name] = result

        decision_logger.log(
            agent_name="Orchestrator",
            action="detect_column_type",
            input_value=column_name,
            output_value=result.agent_type,
            confidence=round(result.confidence * 100, 1),
            upload_id=upload_id,
            details={"detected_type": result.detected_type, "scores": result.scores,
                     "sample_size": result.sample_size},
        )

    return RoutingPlan(column_agents=column_agents, column_results=column_results)


def load_and_route_upload(raw_bytes: bytes, *, filename: str, upload_id: int | None = None) -> tuple[ValidatedUpload, RoutingPlan]:
    """The Phase 3 entry point: validate the raw upload, then build its
    routing plan. Raises an UploadValidationError subclass if the file
    itself is unusable — callers (the future upload route) turn that into a
    clean 4xx instead of a crash."""
    settings = get_settings()
    validated = validate_and_load_upload(
        raw_bytes,
        filename=filename,
        max_bytes=settings.max_upload_size_bytes,
        allowed_extensions=settings.allowed_file_extensions,
    )
    plan = build_routing_plan(validated.df, upload_id=upload_id)
    return validated, plan
