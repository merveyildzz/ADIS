"""The single SQLAlchemy engine/session factory for the whole app.

Every write in this codebase goes through the ORM session created here —
never a hand-built SQL string. Connection-level PRAGMAs make SQLite behave
sanely under concurrent access and enforce foreign keys (off by default in
SQLite, unlike every other engine).
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings, get_settings


class Base(DeclarativeBase):
    pass


def ensure_sqlite_directory_exists(database_url: str) -> None:
    """SQLite silently fails to connect if its parent directory is missing."""
    url = make_url(database_url)
    if url.drivername.startswith("sqlite") and url.database and url.database != ":memory:":
        Path(url.database).resolve().parent.mkdir(parents=True, exist_ok=True)


def _register_sqlite_pragmas(engine: Engine) -> None:
    """Only meaningful for SQLite; other engines enforce these natively."""
    if not engine.url.drivername.startswith("sqlite"):
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        # WAL lets readers and writers proceed concurrently instead of
        # blocking on SQLite's default single-writer file lock.
        cursor.execute("PRAGMA journal_mode=WAL")
        # If a writer does hit lock contention, wait instead of failing
        # immediately with "database is locked".
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


def create_app_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    ensure_sqlite_directory_exists(settings.database_url)
    engine = create_engine(settings.database_url)
    _register_sqlite_pragmas(engine)
    return engine


engine = create_app_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
