"""HTTP surface for Phase 5: upload a file, then read back the cleaned data
(paginated, filterable by confidence) and per-cell lineage. The LLM is only
ever reached through `get_llm_client()` — never imported here directly.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.schemas import (
    AppliedRulesIn,
    AppliedRulesOut,
    CleanedRecordOut,
    CleanedRecordsPageOut,
    CorrectionIn,
    InsightsOut,
    LineageEntryOut,
    LineageOut,
    RuleIn,
    RuleOut,
    RuleViolationOut,
    UploadOut,
    UploadResultOut,
)
from app.config import get_settings
from app.db import repository as repo
from app.db.base import get_db
from app.export import ExportNotAvailableError, build_cleaned_csv, delete_raw_upload_file, save_raw_upload_file
from app.llm.client import get_llm_client
from app.orchestrator.exceptions import UploadValidationError
from app.orchestrator.file_validation import validate_and_load_upload
from app.orchestrator.orchestrator import build_routing_plan
from app.pipeline import run_cleaning_pipeline
from app.rules.reevaluation import apply_rules_to_upload
from app.rules.validation import RuleCreateIn, validate_rule_definition

_SORTABLE_FIELDS = {"record_id", "column_name", "confidence_score", "original_value", "cleaned_value"}

router = APIRouter(prefix="/api")


@router.get("/config")
def get_client_config() -> dict:
    """Upload constraints the frontend needs to display and enforce
    client-side (Phase 9) — sourced from the same Settings the backend
    itself validates against, so the two can never drift apart."""
    settings = get_settings()
    return {
        "max_upload_size_mb": settings.max_upload_size_mb,
        "allowed_file_extensions": list(settings.allowed_file_extensions),
    }


@router.post("/uploads", response_model=UploadResultOut)
def create_upload(db: Session = Depends(get_db), file: UploadFile = File(...)) -> UploadResultOut:
    settings = get_settings()
    raw_bytes = file.file.read()

    try:
        validated = validate_and_load_upload(
            raw_bytes,
            filename=file.filename or "upload.csv",
            max_bytes=settings.max_upload_size_bytes,
            allowed_extensions=settings.allowed_file_extensions,
        )
    except UploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    upload = repo.create_raw_upload(db, filename=file.filename or "upload.csv", row_count=len(validated.df))
    # Kept so the "download cleaned CSV" export can later rebuild the full
    # file (including columns no agent classified) with corrections applied.
    save_raw_upload_file(upload.upload_id, raw_bytes)
    llm_client = get_llm_client()  # single instance, reused for routing and cleaning
    plan = build_routing_plan(validated.df, upload_id=upload.upload_id, llm_client=llm_client)

    try:
        summary = run_cleaning_pipeline(
            db, upload_id=upload.upload_id, df=validated.df, routing_plan=plan, llm_client=llm_client
        )
    except repo.DatabaseWriteError as exc:
        raise HTTPException(status_code=500, detail="Failed to save cleaning results.") from exc

    refreshed = repo.get_raw_upload(db, upload_id=upload.upload_id)
    return UploadResultOut(
        upload=UploadOut.model_validate(refreshed),
        warnings=validated.warnings,
        columns_cleaned=summary["columns_cleaned"],
        columns_unclassified=summary["columns_unclassified"],
        columns_profiled=summary["columns_profiled"],
    )


@router.get("/uploads", response_model=list[UploadOut])
def list_uploads(
    db: Session = Depends(get_db), limit: int = Query(50, ge=1), offset: int = Query(0, ge=0)
) -> list[UploadOut]:
    uploads = repo.list_raw_uploads(db, limit=limit, offset=offset)
    return [UploadOut.model_validate(u) for u in uploads]


@router.get("/uploads/{upload_id}", response_model=UploadOut)
def get_upload(upload_id: int, db: Session = Depends(get_db)) -> UploadOut:
    upload = repo.get_raw_upload(db, upload_id=upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail=f"Upload {upload_id} not found.")
    return UploadOut.model_validate(upload)


@router.delete("/uploads/{upload_id}")
def delete_upload(upload_id: int, db: Session = Depends(get_db)) -> dict:
    if repo.get_raw_upload(db, upload_id=upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Upload {upload_id} not found.")

    try:
        repo.delete_raw_upload(db, upload_id=upload_id)
    except repo.DatabaseWriteError as exc:
        raise HTTPException(status_code=500, detail="Failed to delete upload.") from exc

    delete_raw_upload_file(upload_id)
    return {"deleted": True}


@router.get("/uploads/{upload_id}/cleaned-records", response_model=CleanedRecordsPageOut)
def get_cleaned_records(
    upload_id: int,
    db: Session = Depends(get_db),
    column_name: str | None = None,
    max_confidence: float | None = Query(None, ge=0, le=100, description="Only cells below this confidence"),
    sort_by: str = Query("record_id"),
    sort_dir: str = Query("asc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1),
    offset: int = Query(0, ge=0),
) -> CleanedRecordsPageOut:
    if repo.get_raw_upload(db, upload_id=upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Upload {upload_id} not found.")
    if sort_by not in _SORTABLE_FIELDS:
        raise HTTPException(status_code=400, detail=f"sort_by must be one of {sorted(_SORTABLE_FIELDS)}.")

    records = repo.get_cleaned_records(
        db, upload_id=upload_id, column_name=column_name, max_confidence=max_confidence,
        sort_by=sort_by, sort_dir=sort_dir, limit=limit, offset=offset,
    )
    total = repo.count_cleaned_records(db, upload_id=upload_id, column_name=column_name, max_confidence=max_confidence)
    capped_limit = min(limit, get_settings().max_page_size)

    flagged_record_ids = repo.get_record_ids_with_rule_violations(db, record_ids=[r.record_id for r in records])
    items = []
    for r in records:
        item = CleanedRecordOut.model_validate(r)
        item.has_rule_violation = r.record_id in flagged_record_ids
        items.append(item)

    return CleanedRecordsPageOut(
        items=items,
        total=total,
        limit=capped_limit,
        offset=offset,
    )


@router.get("/uploads/{upload_id}/columns", response_model=list[str])
def get_columns(upload_id: int, db: Session = Depends(get_db)) -> list[str]:
    """Populates the UI's column-filter dropdown — every column that has
    cleaned data for this upload, so the user picks from a real list
    instead of typing a name that may or may not exist."""
    if repo.get_raw_upload(db, upload_id=upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Upload {upload_id} not found.")
    return repo.list_distinct_columns(db, upload_id=upload_id)


@router.get("/uploads/{upload_id}/export")
def export_cleaned_csv(upload_id: int, db: Session = Depends(get_db)) -> Response:
    """The originally uploaded file with every classified column's values
    replaced by their current cleaned_value — read live, so any correction
    already saved is reflected in the very next download, with no separate
    export cache to go stale."""
    upload = repo.get_raw_upload(db, upload_id=upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail=f"Upload {upload_id} not found.")

    try:
        csv_text = build_cleaned_csv(db, upload_id=upload_id)
    except ExportNotAvailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    safe_name = upload.filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or "upload.csv"
    download_name = f"cleaned_{safe_name}"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
    )


@router.get("/uploads/{upload_id}/records/{record_id}/lineage", response_model=LineageOut)
def get_lineage(upload_id: int, record_id: int, db: Session = Depends(get_db)) -> LineageOut:
    record = repo.get_cleaned_record(db, record_id=record_id)
    if record is None or record.upload_id != upload_id:
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found for upload {upload_id}.")

    history = repo.get_lineage_for_record(db, record_id=record_id)
    return LineageOut(
        record=CleanedRecordOut.model_validate(record),
        history=[LineageEntryOut.model_validate(h) for h in history],
        has_lineage=len(history) > 0,
    )


@router.post("/uploads/{upload_id}/records/{record_id}/correction", response_model=CleanedRecordOut)
def submit_correction(
    upload_id: int, record_id: int, correction: CorrectionIn, db: Session = Depends(get_db)
) -> CleanedRecordOut:
    """Phase 6: a user manually fixing a cell both updates that cell and
    teaches the pipeline — the correction is stored in feedback_corrections
    so a future (near-)identical raw value is resolved from it directly,
    without re-running the agent (or, for the Address Agent, without an
    LLM call at all)."""
    record = repo.get_cleaned_record(db, record_id=record_id)
    if record is None or record.upload_id != upload_id:
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found for upload {upload_id}.")

    try:
        updated = repo.submit_correction(db, record_id=record_id, corrected_value=correction.corrected_value)
    except repo.DatabaseWriteError as exc:
        raise HTTPException(status_code=500, detail="Failed to save correction.") from exc

    return CleanedRecordOut.model_validate(updated)


@router.get("/uploads/{upload_id}/insights", response_model=InsightsOut)
def get_insights(upload_id: int, db: Session = Depends(get_db)) -> InsightsOut:
    """Phase 7. Insights are computed once, at cleaning time (see
    pipeline.py) — this just serves the cached result. `available=False`
    (not a 404/500) is the expected, correctly-handled state for an upload
    still processing or one where insight computation failed; the deterministic
    numbers here are exactly what a re-run would produce, since nothing
    about them depends on when they're read."""
    upload = repo.get_raw_upload(db, upload_id=upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail=f"Upload {upload_id} not found.")

    if not upload.insights_json:
        return InsightsOut(available=False)

    cards = json.loads(upload.insights_json)
    return InsightsOut(available=True, **cards)


# --- Custom rules ------------------------------------------------------------


def _rule_to_out(rule) -> RuleOut:
    return RuleOut(
        rule_id=rule.rule_id,
        name=rule.name,
        target_kind=rule.target_kind.value,
        target_value=rule.target_value,
        condition_operator=rule.condition_operator,
        condition_value=json.loads(rule.condition_value) if rule.condition_value is not None else None,
        action=rule.action.value,
        severity=rule.severity,
        created_at=rule.created_at,
        is_active=rule.is_active,
    )


def _get_upload_or_404(db: Session, upload_id: int):
    upload = repo.get_raw_upload(db, upload_id=upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail=f"Upload {upload_id} not found.")
    return upload


def _get_own_rule_or_404(db: Session, *, upload_id: int, rule_id: int):
    rule = repo.get_custom_rule(db, rule_id=rule_id)
    if rule is None or rule.upload_id != upload_id:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found for upload {upload_id}.")
    return rule


@router.get("/uploads/{upload_id}/rules", response_model=list[RuleOut])
def list_rules(upload_id: int, db: Session = Depends(get_db), active_only: bool = False) -> list[RuleOut]:
    _get_upload_or_404(db, upload_id)
    return [_rule_to_out(r) for r in repo.list_custom_rules(db, upload_id=upload_id, active_only=active_only)]


@router.post("/uploads/{upload_id}/rules", response_model=RuleOut)
def create_rule(upload_id: int, rule: RuleIn, db: Session = Depends(get_db)) -> RuleOut:
    _get_upload_or_404(db, upload_id)

    create_in = RuleCreateIn(**rule.model_dump())
    known_columns = set(repo.list_distinct_columns(db, upload_id=upload_id))
    existing = repo.list_custom_rules(db, upload_id=upload_id, active_only=True)
    errors = validate_rule_definition(create_in, known_columns=known_columns, existing_rules=existing)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    created = repo.create_custom_rule(
        db, upload_id=upload_id, name=rule.name, target_kind=rule.target_kind, target_value=rule.target_value,
        condition_operator=rule.condition_operator,
        condition_value=json.dumps(rule.condition_value) if rule.condition_value is not None else None,
        action=rule.action, severity=rule.severity,
    )
    # Deliberately NOT applied yet — defining a rule and turning it on are
    # two separate, explicit steps (see PUT /uploads/{id}/rules/applied).
    return _rule_to_out(created)


# NOTE: these two /rules/applied routes must stay registered before
# /rules/{rule_id} below — Starlette matches path routes in registration
# order, and {rule_id} would otherwise greedily match the literal segment
# "applied" as if it were a rule id (and fail to parse it as an int).
@router.get("/uploads/{upload_id}/rules/applied", response_model=AppliedRulesOut)
def get_applied_rules(upload_id: int, db: Session = Depends(get_db)) -> AppliedRulesOut:
    _get_upload_or_404(db, upload_id)
    return AppliedRulesOut(rule_ids=repo.list_applied_rule_ids_for_upload(db, upload_id=upload_id))


@router.put("/uploads/{upload_id}/rules/applied", response_model=AppliedRulesOut)
def put_applied_rules(upload_id: int, body: AppliedRulesIn, db: Session = Depends(get_db)) -> AppliedRulesOut:
    """Sets the full set of rules turned on for this upload (replaces the
    previous selection). Rules newly turned on are evaluated against this
    upload's already-cleaned records right away; rules turned off have
    their previously-recorded violations for this upload removed."""
    _get_upload_or_404(db, upload_id)

    active_rules = {r.rule_id: r for r in repo.list_custom_rules(db, upload_id=upload_id, active_only=True)}
    unknown = [rid for rid in body.rule_ids if rid not in active_rules]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown, inactive, or not-this-upload's rule id(s): {unknown}")

    try:
        newly_added, _newly_removed = repo.set_applied_rules_for_upload(
            db, upload_id=upload_id, rule_ids=body.rule_ids
        )
    except repo.DatabaseWriteError as exc:
        raise HTTPException(status_code=500, detail="Failed to update applied rules.") from exc

    apply_rules_to_upload(db, upload_id=upload_id, rules=[active_rules[rid] for rid in newly_added])

    return AppliedRulesOut(rule_ids=repo.list_applied_rule_ids_for_upload(db, upload_id=upload_id))


@router.put("/uploads/{upload_id}/rules/{rule_id}", response_model=RuleOut)
def update_rule(upload_id: int, rule_id: int, rule: RuleIn, db: Session = Depends(get_db)) -> RuleOut:
    _get_own_rule_or_404(db, upload_id=upload_id, rule_id=rule_id)

    create_in = RuleCreateIn(**rule.model_dump())
    known_columns = set(repo.list_distinct_columns(db, upload_id=upload_id))
    existing = [
        r for r in repo.list_custom_rules(db, upload_id=upload_id, active_only=True) if r.rule_id != rule_id
    ]
    errors = validate_rule_definition(create_in, known_columns=known_columns, existing_rules=existing)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    try:
        updated = repo.update_custom_rule(
            db, rule_id=rule_id, name=rule.name, target_kind=rule.target_kind, target_value=rule.target_value,
            condition_operator=rule.condition_operator,
            condition_value=json.dumps(rule.condition_value) if rule.condition_value is not None else None,
            action=rule.action, severity=rule.severity,
        )
    except repo.DatabaseWriteError as exc:
        raise HTTPException(status_code=500, detail="Failed to update rule.") from exc
    return _rule_to_out(updated)


@router.delete("/uploads/{upload_id}/rules/{rule_id}")
def delete_rule(upload_id: int, rule_id: int, db: Session = Depends(get_db)) -> dict:
    _get_own_rule_or_404(db, upload_id=upload_id, rule_id=rule_id)
    try:
        repo.delete_custom_rule(db, rule_id=rule_id)
    except repo.DatabaseWriteError as exc:
        raise HTTPException(status_code=500, detail="Failed to delete rule.") from exc
    return {"deleted": True}


@router.get("/uploads/{upload_id}/rule-violations", response_model=list[RuleViolationOut])
def get_rule_violations(upload_id: int, db: Session = Depends(get_db)) -> list[RuleViolationOut]:
    _get_upload_or_404(db, upload_id)

    logs = repo.list_rule_violations_for_upload(db, upload_id=upload_id)
    rule_names: dict[int, str] = {r.rule_id: r.name for r in repo.list_custom_rules(db, upload_id=upload_id)}

    out = []
    for log in logs:
        details = json.loads(log.details) if log.details else {}
        rule_id = details.get("rule_id")
        out.append(RuleViolationOut(
            log_id=log.log_id,
            rule_id=rule_id,
            rule_name=rule_names.get(rule_id),
            column_name=details.get("column"),
            record_id=log.record_id,
            severity=details.get("severity"),
            action=details.get("action"),
            timestamp=log.timestamp,
        ))
    return out
