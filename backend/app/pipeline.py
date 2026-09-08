"""Runs one upload's full cleaning pass: orchestrator routing -> per-column
agent cleaning -> one atomic DB write (cleaned_records + linked audit_log
rows) -> upload status update. This is the glue between Phases 3/4 (routing
+ agents, both in-memory) and Phase 2's persistence layer.
"""
from __future__ import annotations

import json
import logging

import pandas as pd
from sqlalchemy.orm import Session

from app.agents import address_agent, contact_agent, currency_agent, date_agent, numeric_agent
from app.agents.base import AgentResult
from app.db import repository
from app.db.models import UploadStatus
from app.db.repository import CleanedRecordInput, CleaningAuditEntry
from app.llm.client import LLMClient
from app.orchestrator.orchestrator import RoutingPlan, build_routing_plan

logger = logging.getLogger("pipeline")


def _find_currency_hint_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        if "currency" in col.lower() and "hint" in col.lower():
            return col
    return None


def _clean_column(
    column_name: str, agent_type: str, detected_type: str | None, df: pd.DataFrame, llm_client: LLMClient | None
) -> list[AgentResult]:
    series = df[column_name]
    if agent_type == "DateAgent":
        return date_agent.clean_column(series)
    if agent_type == "CurrencyAgent":
        hint_col = _find_currency_hint_column(df)
        hints = df[hint_col] if hint_col else None
        return currency_agent.clean_column(series, currency_hints=hints)
    if agent_type == "ContactAgent":
        column_type = detected_type if detected_type in ("phone", "email") else "email"
        return contact_agent.clean_column(series, column_type)
    if agent_type == "NumericAgent":
        return numeric_agent.clean_column(series)
    if agent_type == "AddressAgent":
        return address_agent.clean_column(series, llm_client=llm_client)
    raise ValueError(f"No cleaner registered for agent_type={agent_type!r}")


def run_cleaning_pipeline(
    db: Session, *, upload_id: int, df: pd.DataFrame, routing_plan: RoutingPlan, llm_client: LLMClient | None = None
) -> dict:
    """Cleans every routed column and writes the results as one transaction
    (see repository.bulk_insert_cleaned_records_with_audit): either the
    whole run lands, or none of it does — no partially-written upload."""
    entries: list[tuple[CleanedRecordInput, CleaningAuditEntry]] = []
    per_column_summary: dict[str, dict] = {}

    for column_name, agent_type in routing_plan.column_agents.items():
        if agent_type == "unclassified":
            continue

        detected_type = routing_plan.column_results[column_name].detected_type
        results = _clean_column(column_name, agent_type, detected_type, df, llm_client)

        flagged_count = 0
        for result in results:
            flagged_count += int(result.flagged)
            # A missing cell arrives here as pandas NaN, not Python None —
            # str(nan) == "nan", which would otherwise get stored and
            # displayed as if "nan" were the literal original text.
            is_missing = result.original_value is None or (
                isinstance(result.original_value, float) and pd.isna(result.original_value)
            )
            entries.append((
                CleanedRecordInput(
                    column_name=column_name,
                    original_value=None if is_missing else str(result.original_value),
                    cleaned_value=result.cleaned_value,
                    confidence_score=result.confidence,
                    agent_type=agent_type,
                ),
                CleaningAuditEntry(
                    agent_name=agent_type,
                    action="clean_value",
                    details=json.dumps({"method": result.method, "flagged": result.flagged, **result.details}),
                ),
            ))

        per_column_summary[column_name] = {
            "agent_type": agent_type,
            "rows_processed": len(results),
            "rows_flagged": flagged_count,
        }

    try:
        repository.bulk_insert_cleaned_records_with_audit(db, upload_id=upload_id, entries=entries)
        repository.set_upload_status(db, upload_id=upload_id, status=UploadStatus.COMPLETED, row_count=len(df))
    except repository.DatabaseWriteError:
        repository.set_upload_status(db, upload_id=upload_id, status=UploadStatus.FAILED)
        raise

    return {
        "upload_id": upload_id,
        "rows": len(df),
        "columns_cleaned": per_column_summary,
        "columns_unclassified": [c for c, a in routing_plan.column_agents.items() if a == "unclassified"],
    }
