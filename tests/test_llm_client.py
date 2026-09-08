import pytest

from app.llm.client import AnthropicLLMClient, GeminiLLMClient, get_llm_client


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
