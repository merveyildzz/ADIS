"""Pydantic response models for the API — kept separate from the ORM models
so the wire format can evolve independently of the storage schema."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class UploadOut(BaseModel):
    upload_id: int
    filename: str
    uploaded_at: datetime
    row_count: int | None
    status: str

    model_config = {"from_attributes": True}


class UploadResultOut(BaseModel):
    upload: UploadOut
    warnings: list[str]
    columns_cleaned: dict
    columns_unclassified: list[str]


class CleanedRecordOut(BaseModel):
    record_id: int
    column_name: str
    original_value: str | None
    cleaned_value: str | None
    confidence_score: float
    agent_type: str
    column_type: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class CorrectionIn(BaseModel):
    corrected_value: str


class CleanedRecordsPageOut(BaseModel):
    items: list[CleanedRecordOut]
    total: int
    limit: int
    offset: int


class LineageEntryOut(BaseModel):
    log_id: int
    agent_name: str
    action: str
    details: str | None
    timestamp: datetime

    model_config = {"from_attributes": True}


class LineageOut(BaseModel):
    record: CleanedRecordOut
    history: list[LineageEntryOut]
    has_lineage: bool
