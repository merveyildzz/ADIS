"""DB connectivity check for startup validation.

Full schema/session management arrives in Phase 2. For now this only proves
the configured database is reachable, so the app can fail gracefully at
startup rather than surfacing a raw connection error mid-request later.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings


class DatabaseUnavailableError(Exception):
    """Raised when the configured database cannot be reached."""


def _ensure_sqlite_directory_exists(database_url: str) -> None:
    """SQLite silently fails to connect if its parent directory is missing.
    For file-based SQLite URLs, create that directory up front so a fresh
    checkout doesn't fail startup just because `data/` hasn't been made yet.
    """
    url = make_url(database_url)
    if url.drivername.startswith("sqlite") and url.database and url.database != ":memory:":
        Path(url.database).resolve().parent.mkdir(parents=True, exist_ok=True)


def check_database_connection(settings: Settings) -> None:
    try:
        _ensure_sqlite_directory_exists(settings.database_url)
        engine = create_engine(settings.database_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
    except SQLAlchemyError as exc:
        raise DatabaseUnavailableError(
            f"Could not connect to the database at '{settings.database_url}'. "
            "Check DATABASE_URL and that the target file/server is reachable."
        ) from exc
