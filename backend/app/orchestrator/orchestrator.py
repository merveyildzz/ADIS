"""Ties file validation + column-type detection into one entry point: hand
it raw upload bytes, get back a validated DataFrame and a routing plan of
{column_name: agent_type} for the Phase 4 cleaning agents to consume.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from app.config import get_settings
from app.core.logging_config import get_decision_logger
from app.orchestrator.column_detection import (
    ColumnTypeResult,
    build_column_profile,
    detect_column_type,
)
from app.orchestrator.file_validation import ValidatedUpload, validate_and_load_upload
from app.orchestrator.llm_column_classifier import classify_column_via_llm
from app.llm.client import LLMClient

_LLM_CLASSIFIER_SAMPLE_SIZE = 20


@dataclass
class RoutingPlan:
    column_agents: dict[str, str]  # column_name -> agent_type ("unclassified" if none matched)
    column_results: dict[str, ColumnTypeResult]
    warnings: list[str] = field(default_factory=list)


def _sample_for_llm(series: pd.Series) -> list[str]:
    non_null = series.dropna().astype(str)
    non_null = non_null[non_null.str.strip() != ""]
    return non_null.head(_LLM_CLASSIFIER_SAMPLE_SIZE).tolist()


def build_routing_plan(
    df: pd.DataFrame, *, upload_id: int | None = None, llm_client: LLMClient | None = None
) -> RoutingPlan:
    column_agents: dict[str, str] = {}
    column_results: dict[str, ColumnTypeResult] = {}
    decision_logger = get_decision_logger()

    for column_name in df.columns:
        result = detect_column_type(df[column_name], column_name)

        if result.agent_type == "unclassified" and result.detected_type != "categorical":
            # A column no deterministic detector could confidently place
            # (and that isn't already understood as a bounded categorical
            # enum) gets one LLM classification attempt — column name plus
            # a small value sample only, never the full dataset. Logged as
            # its own distinct audit entry, separate from per-cell cleaning
            # confidence, whether or not the LLM is even configured.
            sample_values = _sample_for_llm(df[column_name])
            llm_result = classify_column_via_llm(column_name, sample_values, llm_client)
            decision_logger.log(
                agent_name="Orchestrator",
                action="llm_assisted_classification",
                input_value=column_name,
                output_value=llm_result.agent_type if llm_result else "unknown",
                confidence=llm_result.confidence if llm_result else 0.0,
                upload_id=upload_id,
                details={"llm_available": llm_client is not None, "sample_size": len(sample_values)},
            )
            if llm_result is not None and llm_result.agent_type != "unknown":
                result = ColumnTypeResult(
                    column_name=column_name,
                    detected_type=result.detected_type,
                    agent_type=llm_result.agent_type,
                    confidence=llm_result.confidence / 100.0,
                    sample_size=result.sample_size,
                    scores=result.scores,
                )
            else:
                profile = build_column_profile(df[column_name])
                profile.llm_attempted = llm_client is not None
                profile.llm_agent_guess = llm_result.agent_type if llm_result else None
                result.profile = profile
        elif result.agent_type == "unclassified":
            # Already understood as a bounded categorical enum — no LLM
            # call needed, but still profiled rather than left bare.
            result.profile = build_column_profile(df[column_name])

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
