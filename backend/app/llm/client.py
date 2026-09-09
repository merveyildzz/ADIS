"""The single, isolated entry point for every LLM call in this codebase.

Nothing outside this module ever imports the `anthropic` package directly —
not a route handler, not an agent. That containment is what makes it
possible to guarantee every LLM-backed feature degrades to a non-LLM
fallback: callers only ever see `get_llm_client()` return `None` or a real
client, never a raw SDK exception.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Protocol, TypeVar

from pydantic import BaseModel

from app.config import get_settings

logger = logging.getLogger("llm")

T = TypeVar("T", bound=BaseModel)

ANTHROPIC_MODEL_ID = "claude-opus-5"
# A rolling alias (always resolves to Google's current flash-tier model)
# rather than a dated snapshot id — a hardcoded "gemini-2.5-flash" was
# found to 404 ("no longer available to new users") once Google retired it,
# silently degrading every LLM-backed feature to its non-LLM fallback with
# no code change on our side. The alias avoids that class of failure.
GEMINI_MODEL_ID = "gemini-flash-latest"

# Module-level (per-process) circuit breaker. A single upload can trigger a
# dozen+ LLM calls (one per unclassified column, plus AddressAgent rows) —
# on a rate/quota-limited key, retrying every single one is what made an
# upload take minutes: the SDK's own retry policy (5 attempts, up to 60s
# backoff each) was hit fresh on every call. Once *any* call reports a
# rate/quota error, every subsequent call within the cooldown window is
# short-circuited to the non-LLM fallback immediately, without touching the
# network — a daily quota isn't going to recover in the next few seconds
# regardless of how many times we ask.
_RATE_LIMIT_COOLDOWN_SECONDS = 300.0
_rate_limited_until = 0.0
_RATE_LIMIT_MARKERS = ("429", "resource_exhausted", "rate_limit", "rate limit", "quota")


def _is_rate_limited() -> bool:
    return time.monotonic() < _rate_limited_until


def _looks_like_rate_limit(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _RATE_LIMIT_MARKERS)


def _note_failure(exc: Exception) -> None:
    global _rate_limited_until
    if _looks_like_rate_limit(exc):
        _rate_limited_until = time.monotonic() + _RATE_LIMIT_COOLDOWN_SECONDS
        logger.warning(
            "LLM provider reported a rate/quota limit — pausing all further LLM calls for "
            "%.0fs so the rest of this run doesn't wait on calls that are certain to fail too.",
            _RATE_LIMIT_COOLDOWN_SECONDS,
        )
    logger.exception("LLM structured extraction failed; caller must fall back to a non-LLM path.")


class LLMClient(Protocol):
    def extract_structured(self, *, system_prompt: str, data: dict, response_model: type[T]) -> T | None:
        """Returns a validated instance of `response_model`, or None if the
        call failed or the response didn't validate. `data` is always sent
        as an isolated JSON payload — never string-concatenated into the
        prompt — so untrusted cell content can't be mistaken for
        instructions (prompt-injection defense)."""
        ...


class AnthropicLLMClient:
    def __init__(self, api_key: str, model: str = ANTHROPIC_MODEL_ID) -> None:
        import anthropic  # imported lazily so the package is only required when this provider is actually selected

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def extract_structured(self, *, system_prompt: str, data: dict, response_model: type[T]) -> T | None:
        if _is_rate_limited():
            return None
        try:
            response = self._client.messages.parse(
                model=self._model,
                max_tokens=1024,
                system=system_prompt,
                # The untrusted value travels only inside this JSON payload,
                # as a data field — never spliced into `system_prompt`.
                messages=[{"role": "user", "content": json.dumps(data)}],
                output_format=response_model,
            )
            return response.parsed_output
        except Exception as exc:
            _note_failure(exc)
            return None


class GeminiLLMClient:
    def __init__(self, api_key: str, model: str = GEMINI_MODEL_ID) -> None:
        from google import genai  # imported lazily so the package is only required when this provider is actually selected

        # The SDK's own default retry policy (5 attempts, exponential
        # backoff up to 60s, retrying on 429 among other codes) is exactly
        # wrong for a daily quota limit — every retry is certain to fail
        # too, and with it a single call could block for over a minute.
        # attempts=1 means "try once, fail fast" — we already have a
        # non-LLM fallback for exactly this case; the module-level circuit
        # breaker above (`_note_failure`) then skips the network entirely
        # for subsequent calls once a rate/quota error is seen.
        self._client = genai.Client(
            api_key=api_key,
            http_options=genai.types.HttpOptions(retry_options=genai.types.HttpRetryOptions(attempts=1)),
        )
        self._model = model

    def extract_structured(self, *, system_prompt: str, data: dict, response_model: type[T]) -> T | None:
        from google.genai import types

        if _is_rate_limited():
            return None
        try:
            response = self._client.models.generate_content(
                model=self._model,
                # The untrusted value travels only inside this JSON payload,
                # as a data field — never spliced into `system_instruction`.
                contents=json.dumps(data),
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=response_model,
                ),
            )
            parsed = response.parsed
            if isinstance(parsed, response_model):
                return parsed
            # Fallback in case the SDK returns an unparsed dict/None for this version.
            return response_model.model_validate_json(response.text)
        except Exception as exc:
            _note_failure(exc)
            return None


def get_llm_client() -> LLMClient | None:
    """Returns None when no API key is configured — every caller of this
    function is required to have a working non-LLM fallback for that case."""
    settings = get_settings()
    if not settings.llm_enabled:
        return None
    provider = settings.llm_provider.lower()
    if provider == "gemini":
        return GeminiLLMClient(api_key=settings.llm_api_key, model=settings.llm_model or GEMINI_MODEL_ID)
    if provider == "anthropic":
        return AnthropicLLMClient(api_key=settings.llm_api_key, model=settings.llm_model or ANTHROPIC_MODEL_ID)
    raise ValueError(f"Unknown LLM_PROVIDER={settings.llm_provider!r}; expected 'anthropic' or 'gemini'.")
