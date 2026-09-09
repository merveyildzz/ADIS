"""Pydantic response models for the API — kept separate from the ORM models
so the wire format can evolve independently of the storage schema."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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
    # "Profiled but not transformed": every unclassified column no detector
    # (and no LLM fallback) could confidently place, reported with useful
    # detail (null%, unique count, dtype, min/max) instead of a bare label.
    # Additive — always present, empty when nothing needed profiling.
    columns_profiled: dict = {}


class CleanedRecordOut(BaseModel):
    record_id: int
    column_name: str
    original_value: str | None
    cleaned_value: str | None
    confidence_score: float
    agent_type: str
    column_type: str | None
    created_at: datetime
    has_rule_violation: bool = False

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


class InsightsOut(BaseModel):
    available: bool
    generated_at: str | None = None
    correlations: list[dict] = []
    trends: list[dict] = []
    anomalies: list[dict] = []
    anomaly_count: int = 0
    warnings: list[str] = []
    charts: dict = {}


class RuleIn(BaseModel):
    name: str
    target_kind: Literal["column_name", "detected_type"]
    target_value: str
    condition_operator: Literal["gte", "lte", "eq", "in", "regex_match", "not_null"]
    condition_value: float | int | str | list[str] | None = None
    action: Literal["flag", "reject"] = "flag"
    severity: Literal["low", "medium", "high"] = "medium"


class RuleOut(BaseModel):
    rule_id: int
    name: str
    target_kind: str
    target_value: str
    condition_operator: str
    condition_value: Any | None = None
    action: str
    severity: str
    created_at: datetime
    is_active: bool

    model_config = {"from_attributes": True}


class RuleViolationOut(BaseModel):
    log_id: int
    rule_id: int | None
    rule_name: str | None
    column_name: str | None
    record_id: int | None
    severity: str | None
    action: str | None
    timestamp: datetime
