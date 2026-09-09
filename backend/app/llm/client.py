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
        except Exception:
            logger.exception("LLM structured extraction failed; caller must fall back to a non-LLM path.")
            return None


class GeminiLLMClient:
    def __init__(self, api_key: str, model: str = GEMINI_MODEL_ID) -> None:
        from google import genai  # imported lazily so the package is only required when this provider is actually selected

        self._client = genai.Client(api_key=api_key)
        self._model = model

    def extract_structured(self, *, system_prompt: str, data: dict, response_model: type[T]) -> T | None:
        from google.genai import types

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
        except Exception:
            logger.exception("LLM structured extraction failed; caller must fall back to a non-LLM path.")
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
