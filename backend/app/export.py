"""Rebuilds the cleaned file for download: the originally uploaded CSV with
every classified column's values swapped for their current `cleaned_value`
(always read live from `cleaned_records`, so a user correction is reflected
the moment it's saved — no separate export cache to invalidate). Columns no
agent classified are left exactly as uploaded.
"""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import repository
from app.orchestrator.file_validation import decode_with_fallback


class ExportNotAvailableError(Exception):
    """Raised when the original uploaded file can't be found on disk
    (e.g. an upload from before this feature existed)."""


def raw_file_path(upload_id: int) -> Path:
    return Path(get_settings().uploads_dir) / f"{upload_id}.csv"


def save_raw_upload_file(upload_id: int, raw_bytes: bytes) -> None:
    path = raw_file_path(upload_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw_bytes)


def delete_raw_upload_file(upload_id: int) -> None:
    """Best-effort cleanup when an upload is deleted — missing_ok=True since
    an upload from before this feature existed may have no file on disk at
    all, and that must not block the DB-side delete."""
    raw_file_path(upload_id).unlink(missing_ok=True)


def build_cleaned_csv(db: Session, *, upload_id: int) -> str:
    path = raw_file_path(upload_id)
    if not path.exists():
        raise ExportNotAvailableError(
            f"Original file for upload {upload_id} is no longer available; cannot rebuild the cleaned export."
        )

    text, _encoding = decode_with_fallback(path.read_bytes())
    df = pd.read_csv(io.StringIO(text), keep_default_na=False, na_values=[""])

    records = repository.list_all_cleaned_records_for_upload(db, upload_id=upload_id)
    by_column: dict[str, dict[int, str | None]] = {}
    for r in records:
        by_column.setdefault(r.column_name, {})[r.row_index] = r.cleaned_value

    for column_name, by_row in by_column.items():
        if column_name not in df.columns:
            continue
        df[column_name] = [by_row.get(i, df[column_name].iloc[i]) for i in range(len(df))]

    buffer = io.StringIO()
    df.to_csv(buffer, index=False)
    return buffer.getvalue()
