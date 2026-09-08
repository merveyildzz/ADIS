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

MODEL_ID = "claude-opus-5"


class LLMClient(Protocol):
    def extract_structured(self, *, system_prompt: str, data: dict, response_model: type[T]) -> T | None:
        """Returns a validated instance of `response_model`, or None if the
        call failed or the response didn't validate. `data` is always sent
        as an isolated JSON payload — never string-concatenated into the
        prompt — so untrusted cell content can't be mistaken for
        instructions (prompt-injection defense)."""
        ...


class AnthropicLLMClient:
    def __init__(self, api_key: str) -> None:
        import anthropic  # imported lazily so the package is only required when an LLM key is actually configured

        self._client = anthropic.Anthropic(api_key=api_key)

    def extract_structured(self, *, system_prompt: str, data: dict, response_model: type[T]) -> T | None:
        try:
            response = self._client.messages.parse(
                model=MODEL_ID,
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


def get_llm_client() -> LLMClient | None:
    """Returns None when no API key is configured — every caller of this
    function is required to have a working non-LLM fallback for that case."""
    settings = get_settings()
    if not settings.llm_enabled:
        return None
    return AnthropicLLMClient(api_key=settings.llm_api_key)
