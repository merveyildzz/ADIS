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

from app.agents import address_agent, contact_agent, currency_agent, date_agent, numeric_agent, quantity_agent
from app.agents.base import AgentResult, build_feedback_map
from app.db import repository
from app.db.models import UploadStatus
from app.db.repository import CleanedRecordInput, CleaningAuditEntry
from app.insights.pipeline import build_analysis_dataframe, compute_insight_cards
from app.llm.client import LLMClient
from app.orchestrator.orchestrator import RoutingPlan, build_routing_plan
from app.rules.engine import evaluate_rules_for_upload

logger = logging.getLogger("pipeline")

MAX_CATEGORY_UNIQUE_VALUES = 50
MAX_CATEGORY_UNIQUE_RATIO = 0.1


def _find_currency_hint_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        if "currency" in col.lower() and "hint" in col.lower():
            return col
    return None


def _choose_event_date_column(analysis_df: pd.DataFrame, date_columns: list[str]) -> str | None:
    """When a file has multiple date columns (e.g. `signup_date` and
    `order_date`), prefer the one with the narrowest span. A transactional/
    event date for period-over-period trend analysis is typically a tighter
    recent window; a longer-tenured column like a signup date spans years
    and produces noisy, less meaningful monthly trends."""
    if not date_columns:
        return None
    if len(date_columns) == 1:
        return date_columns[0]

    def span(col: str) -> pd.Timedelta:
        series = analysis_df[col].dropna()
        return series.max() - series.min() if not series.empty else pd.Timedelta.max

    return min(date_columns, key=span)


def _detect_category_column(df: pd.DataFrame, exclude: set[str]) -> str | None:
    """A low-cardinality text column not otherwise classified — e.g.
    `category`, not `customer_id` or free-text `name`. Best-effort heuristic
    for Phase 7's per-category trend view. Among qualifying candidates,
    prefers the one with the *most* distinct values: a 2-3-value column is
    more often an auxiliary flag/hint field (e.g. `currency_hint`) than a
    rich category breakdown, so higher cardinality (within the cap) is the
    better signal of "this is the interesting business dimension"."""
    if len(df) == 0:
        return None
    best_col, best_unique = None, -1
    for col in df.columns:
        if col in exclude:
            continue
        series = df[col]
        # pandas 3.0 defaults string columns to its new StringDtype ("str"),
        # not the classic "object" dtype — is_string_dtype covers both.
        if not (pd.api.types.is_string_dtype(series) or isinstance(series.dtype, pd.CategoricalDtype)):
            continue
        n_unique = series.nunique(dropna=True)
        if 2 <= n_unique <= MAX_CATEGORY_UNIQUE_VALUES and n_unique / len(df) < MAX_CATEGORY_UNIQUE_RATIO:
            if n_unique > best_unique:
                best_col, best_unique = col, n_unique
    return best_col


def _load_feedback_map(db: Session, column_type: str | None) -> dict[str, str]:
    """Phase 6: if the feedback table is empty or the query itself fails,
    cleaning must fall back to normal agent behavior — never crash the run
    over a lookup that's purely an optimization."""
    if not column_type:
        return {}
    try:
        corrections = repository.list_feedback_corrections(db, column_type=column_type)
        return build_feedback_map(corrections)
    except Exception:
        logger.exception("Feedback lookup failed for column_type=%r; continuing without it.", column_type)
        return {}


def _clean_column(
    column_name: str,
    agent_type: str,
    detected_type: str | None,
    df: pd.DataFrame,
    llm_client: LLMClient | None,
    feedback_map: dict[str, str],
) -> list[AgentResult]:
    series = df[column_name]
    if agent_type == "DateAgent":
        return date_agent.clean_column(series, feedback_map=feedback_map)
    if agent_type == "CurrencyAgent":
        hint_col = _find_currency_hint_column(df)
        hints = df[hint_col] if hint_col else None
        return currency_agent.clean_column(series, currency_hints=hints, feedback_map=feedback_map)
    if agent_type == "ContactAgent":
        column_type = detected_type if detected_type in ("phone", "email") else "email"
        return contact_agent.clean_column(series, column_type, feedback_map=feedback_map)
    if agent_type == "NumericAgent":
        return numeric_agent.clean_column(series, feedback_map=feedback_map)
    if agent_type == "AddressAgent":
        return address_agent.clean_column(series, llm_client=llm_client, feedback_map=feedback_map)
    if agent_type == "QuantityAgent":
        return quantity_agent.clean_column(series, feedback_map=feedback_map)
    raise ValueError(f"No cleaner registered for agent_type={agent_type!r}")


def run_cleaning_pipeline(
    db: Session, *, upload_id: int, df: pd.DataFrame, routing_plan: RoutingPlan, llm_client: LLMClient | None = None
) -> dict:
    """Cleans every routed column and writes the results as one transaction
    (see repository.bulk_insert_cleaned_records_with_audit): either the
    whole run lands, or none of it does — no partially-written upload."""
    entries: list[tuple[CleanedRecordInput, CleaningAuditEntry]] = []
    per_column_summary: dict[str, dict] = {}
    feedback_map_cache: dict[str, dict[str, str]] = {}
    cleaned_columns_for_analysis: dict[str, tuple[str | None, list]] = {}
    columns_profiled: dict[str, dict] = {}

    for column_name, agent_type in routing_plan.column_agents.items():
        if agent_type == "unclassified":
            # "Profiled but not transformed": every unclassified column
            # (whether no detector matched, it scored as a bounded
            # categorical enum, or the LLM fallback also came back
            # "unknown") is reported in useful detail here rather than
            # silently dropped — see column_detection.build_column_profile.
            profile = routing_plan.column_results[column_name].profile
            if profile is not None:
                columns_profiled[column_name] = {
                    "detected_type": routing_plan.column_results[column_name].detected_type,
                    "null_pct": profile.null_pct,
                    "unique_count": profile.unique_count,
                    "inferred_dtype": profile.inferred_dtype,
                    "min_value": profile.min_value,
                    "max_value": profile.max_value,
                    "llm_attempted": profile.llm_attempted,
                    "llm_agent_guess": profile.llm_agent_guess,
                }
            continue

        detected_type = routing_plan.column_results[column_name].detected_type
        if detected_type not in feedback_map_cache:
            feedback_map_cache[detected_type] = _load_feedback_map(db, detected_type)
        feedback_map = feedback_map_cache[detected_type]

        results = _clean_column(column_name, agent_type, detected_type, df, llm_client, feedback_map)
        cleaned_columns_for_analysis[column_name] = (detected_type, [r.cleaned_value for r in results])

        flagged_count = 0
        influenced_by_feedback_count = 0
        for row_idx, result in enumerate(results):
            flagged_count += int(result.flagged)
            influenced_by_feedback_count += int(result.details.get("influenced_by_prior_feedback", False))
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
                    column_type=detected_type,
                    row_index=row_idx,
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
            "rows_influenced_by_feedback": influenced_by_feedback_count,
        }

    try:
        record_ids = repository.bulk_insert_cleaned_records_with_audit(db, upload_id=upload_id, entries=entries)
        repository.set_upload_status(db, upload_id=upload_id, status=UploadStatus.COMPLETED, row_count=len(df))
    except repository.DatabaseWriteError:
        repository.set_upload_status(db, upload_id=upload_id, status=UploadStatus.FAILED)
        raise

    # Custom rules: evaluated against the already-cleaned values (never the
    # raw ones), written as audit_log rows on the same record_id an agent's
    # cleaning decision already attached to that cell — a rule violation is
    # just another kind of lineage event, not a separate system. Enrichment
    # on top of an already-successful cleaning run: a bug here must never
    # fail the upload, mirroring the Phase 7 insights block below.
    try:
        record_ids_by_column_row = {
            (entry.column_name, entry.row_index): record_id
            for (entry, _audit), record_id in zip(entries, record_ids)
        }
        active_rules = repository.list_active_rules(db)
        violations = evaluate_rules_for_upload(active_rules, cleaned_columns_for_analysis)
        repository.insert_rule_violation_audit_logs(
            db, upload_id=upload_id,
            record_ids_by_column_row=record_ids_by_column_row,
            violations=violations,
        )
    except Exception:
        logger.exception("Rule evaluation failed for upload %s; cleaning results are unaffected.", upload_id)

    # Phase 7: computed once, right here, while the full original DataFrame
    # (including columns no agent classified, e.g. `category`) is still in
    # memory — never persisted raw anywhere, so this is the only chance to
    # analyze them. Insight computation is enrichment, not core cleaning: a
    # failure here must not fail the upload that already succeeded above.
    try:
        analysis_df = build_analysis_dataframe(df, cleaned_columns_for_analysis)
        date_columns = [c for c, (t, _) in cleaned_columns_for_analysis.items() if t == "date"]
        numeric_columns = [
            c for c, (t, _) in cleaned_columns_for_analysis.items() if t in ("currency", "numeric_age", "quantity")
        ]
        # Only currency columns are meaningful to *sum* over time (revenue);
        # summing e.g. ages has no business meaning, even though age is a
        # perfectly good numeric column for correlation/anomaly detection.
        trend_numeric_columns = [c for c, (t, _) in cleaned_columns_for_analysis.items() if t == "currency"]
        category_column = _detect_category_column(df, exclude=set(cleaned_columns_for_analysis.keys()))
        insight_cards = compute_insight_cards(
            analysis_df,
            date_column=_choose_event_date_column(analysis_df, date_columns),
            category_column=category_column,
            numeric_columns=numeric_columns,
            trend_numeric_columns=trend_numeric_columns,
            llm_client=llm_client,
        )
        repository.save_insights(db, upload_id=upload_id, insights_json=json.dumps(insight_cards))
    except Exception:
        logger.exception("Insight computation failed for upload %s; cleaning results are unaffected.", upload_id)

    return {
        "upload_id": upload_id,
        "rows": len(df),
        "columns_cleaned": per_column_summary,
        "columns_unclassified": [c for c, a in routing_plan.column_agents.items() if a == "unclassified"],
        "columns_profiled": columns_profiled,
    }
