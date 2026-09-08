"""DB connectivity check for startup validation — proves the configured
database is reachable before the app starts serving, so a bad/missing
DATABASE_URL fails with a clear message instead of a raw error mid-request.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


class DatabaseUnavailableError(Exception):
    """Raised when the configured database cannot be reached."""


def check_database_connection(engine: Engine) -> None:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise DatabaseUnavailableError(
            f"Could not connect to the database at '{engine.url}'. "
            "Check DATABASE_URL and that the target file/server is reachable."
        ) from exc
