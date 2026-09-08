import pytest

from app.config import ConfigError, Settings, get_settings


def test_default_settings_load_without_env():
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.database_url
    assert settings.max_upload_size_mb > 0
    assert settings.llm_enabled is False


def test_missing_llm_key_is_not_fatal():
    """LLM is optional by design: no key means fallback mode, not a crash."""
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.llm_api_key is None
    assert settings.llm_enabled is False


def test_invalid_upload_size_raises_config_error(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "-5")
    get_settings.cache_clear()
    with pytest.raises(ConfigError):
        get_settings()
    get_settings.cache_clear()


def test_empty_database_url_raises_config_error(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    get_settings.cache_clear()
    with pytest.raises(ConfigError):
        get_settings()
    get_settings.cache_clear()


def test_confidence_thresholds_match_roadmap_bands():
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.confidence_high_threshold == 90
    assert settings.confidence_medium_threshold == 60
