import pytest
from pydantic import BaseModel

import app.llm.client as llm_client_module
from app.llm.client import AnthropicLLMClient, GeminiLLMClient, get_llm_client


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_rate_limit_circuit_breaker():
    # Module-level state shared across every LLMClient instance in this
    # process — must not leak between tests (or into whichever test runs
    # after this file, since pytest shares one process for the whole suite).
    llm_client_module._rate_limited_until = 0.0
    yield
    llm_client_module._rate_limited_until = 0.0


def test_get_llm_client_returns_none_when_no_api_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert get_llm_client() is None


def test_get_llm_client_defaults_to_anthropic(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake-key")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    client = get_llm_client()
    assert isinstance(client, AnthropicLLMClient)


def test_get_llm_client_selects_gemini_when_configured(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake-key")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    client = get_llm_client()
    assert isinstance(client, GeminiLLMClient)


def test_get_llm_client_is_case_insensitive_on_provider(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake-key")
    monkeypatch.setenv("LLM_PROVIDER", "Gemini")
    assert isinstance(get_llm_client(), GeminiLLMClient)


def test_get_llm_client_raises_clear_error_for_unknown_provider(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake-key")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        get_llm_client()


def test_get_llm_client_honors_model_override(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake-key")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_MODEL", "gemini-custom-model")
    client = get_llm_client()
    assert client._model == "gemini-custom-model"


# --- Rate/quota-limit circuit breaker -----------------------------------------
#
# A single upload can trigger a dozen+ LLM calls (one per unclassified
# column, plus per-row AddressAgent calls) — on a rate/quota-limited key,
# retrying every single one made an upload take minutes. These tests cover
# the fix: once one call reports a rate/quota error, every subsequent call
# within the cooldown window is skipped without touching the network.


class _DummyResponseModel(BaseModel):
    value: str


def test_looks_like_rate_limit_recognizes_quota_and_429_errors():
    assert llm_client_module._looks_like_rate_limit(Exception("429 RESOURCE_EXHAUSTED: quota exceeded")) is True
    assert llm_client_module._looks_like_rate_limit(Exception("rate_limit_error: too many requests")) is True
    assert llm_client_module._looks_like_rate_limit(Exception("connection reset by peer")) is False


def test_note_failure_on_rate_limit_error_arms_the_circuit_breaker():
    assert llm_client_module._is_rate_limited() is False
    llm_client_module._note_failure(Exception("429 RESOURCE_EXHAUSTED"))
    assert llm_client_module._is_rate_limited() is True


def test_note_failure_on_unrelated_error_does_not_arm_the_circuit_breaker():
    llm_client_module._note_failure(Exception("some unrelated transient error"))
    assert llm_client_module._is_rate_limited() is False


def test_extract_structured_short_circuits_when_already_rate_limited(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake-key")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    client = get_llm_client()

    calls = []
    monkeypatch.setattr(
        client._client.models, "generate_content", lambda **kwargs: calls.append(kwargs) or (_ for _ in ()).throw(AssertionError("should never be called"))
    )

    llm_client_module._note_failure(Exception("429 RESOURCE_EXHAUSTED"))
    result = client.extract_structured(system_prompt="x", data={"a": 1}, response_model=_DummyResponseModel)

    assert result is None
    assert calls == []  # the network call was skipped entirely


def test_gemini_client_disables_sdk_retries():
    # The SDK's own default retry policy (5 attempts, backoff up to 60s,
    # retrying on 429 among other codes) is exactly wrong for a daily quota
    # limit — every retry is certain to fail too. attempts=1 means "try
    # once, fail fast"; our own fallback (returning None) covers the rest.
    client = GeminiLLMClient(api_key="fake-key")
    retry_options = client._client._api_client._http_options.retry_options
    assert retry_options.attempts == 1
