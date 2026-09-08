"""FastAPI application entrypoint.

Phase 0 concerns only: load + validate configuration, verify DB connectivity,
wire up logging, and guarantee that no unhandled exception ever reaches the
client as a raw stack trace.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import ConfigError, get_settings
from app.core.db import DatabaseUnavailableError, check_database_connection
from app.core.logging_config import setup_logging

logger = logging.getLogger("app")


def create_app() -> FastAPI:
    # Config is loaded first: if it's missing/invalid, fail immediately with
    # a clear message instead of starting an app that will break on first use.
    try:
        settings = get_settings()
    except ConfigError as exc:
        # No logger yet (logging needs settings.log_level) — this is the one
        # place we print directly, deliberately, before anything else exists.
        print(f"[startup] Configuration error: {exc}")
        raise SystemExit(1) from exc

    setup_logging()

    if not settings.llm_enabled:
        logger.warning(
            "LLM_API_KEY not set — LLM-backed agents (Address, Narrative) "
            "will run in non-LLM fallback mode."
        )

    try:
        check_database_connection(settings)
    except DatabaseUnavailableError as exc:
        logger.critical(str(exc))
        raise SystemExit(1) from exc

    app = FastAPI(title="AI-Powered Data Cleaning & Insight Platform")

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Full details go to the server log only; the client gets a generic,
        # non-leaking message. Never expose a stack trace to the end user.
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": "An unexpected error occurred. Please try again."},
        )

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "llm_enabled": settings.llm_enabled,
            "environment": settings.environment,
        }

    return app


app = create_app()
