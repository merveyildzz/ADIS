"""HTTP surface for Phase 5: upload a file, then read back the cleaned data
(paginated, filterable by confidence) and per-cell lineage. The LLM is only
ever reached through `get_llm_client()` — never imported here directly.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.schemas import (
    CleanedRecordOut,
    CleanedRecordsPageOut,
    LineageEntryOut,
    LineageOut,
    UploadOut,
    UploadResultOut,
)
from app.config import get_settings
from app.db import repository as repo
from app.db.base import get_db
from app.llm.client import get_llm_client
from app.orchestrator.exceptions import UploadValidationError
from app.orchestrator.file_validation import validate_and_load_upload
from app.orchestrator.orchestrator import build_routing_plan
from app.pipeline import run_cleaning_pipeline

router = APIRouter(prefix="/api")


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
    plan = build_routing_plan(validated.df, upload_id=upload.upload_id)

    try:
        summary = run_cleaning_pipeline(
            db, upload_id=upload.upload_id, df=validated.df, routing_plan=plan, llm_client=get_llm_client()
        )
    except repo.DatabaseWriteError as exc:
        raise HTTPException(status_code=500, detail="Failed to save cleaning results.") from exc

    refreshed = repo.get_raw_upload(db, upload_id=upload.upload_id)
    return UploadResultOut(
        upload=UploadOut.model_validate(refreshed),
        warnings=validated.warnings,
        columns_cleaned=summary["columns_cleaned"],
        columns_unclassified=summary["columns_unclassified"],
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


@router.get("/uploads/{upload_id}/cleaned-records", response_model=CleanedRecordsPageOut)
def get_cleaned_records(
    upload_id: int,
    db: Session = Depends(get_db),
    column_name: str | None = None,
    max_confidence: float | None = Query(None, ge=0, le=100, description="Only cells below this confidence"),
    limit: int = Query(50, ge=1),
    offset: int = Query(0, ge=0),
) -> CleanedRecordsPageOut:
    if repo.get_raw_upload(db, upload_id=upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Upload {upload_id} not found.")

    records = repo.get_cleaned_records(
        db, upload_id=upload_id, column_name=column_name, max_confidence=max_confidence, limit=limit, offset=offset
    )
    total = repo.count_cleaned_records(db, upload_id=upload_id, column_name=column_name, max_confidence=max_confidence)
    capped_limit = min(limit, get_settings().max_page_size)
    return CleanedRecordsPageOut(
        items=[CleanedRecordOut.model_validate(r) for r in records],
        total=total,
        limit=capped_limit,
        offset=offset,
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
