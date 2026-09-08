import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from app import config

    config.get_settings.cache_clear()

    from app import main as main_module

    importlib.reload(main_module)
    return TestClient(main_module.app)


def test_health_endpoint_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["llm_enabled"] is False


def test_missing_sqlite_directory_is_created_automatically(monkeypatch, tmp_path):
    # A fresh checkout won't have data/ yet — this must not be treated as a
    # fatal "invalid" DB config; the directory should just get created.
    nested_path = tmp_path / "does" / "not" / "exist" / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{nested_path}")

    from app import config

    config.get_settings.cache_clear()

    from app import main as main_module

    importlib.reload(main_module)  # must not raise
    assert nested_path.exists()
    config.get_settings.cache_clear()


def test_malformed_database_url_fails_startup_gracefully(monkeypatch):
    # A genuinely broken DB config must fail startup with a clear, logged
    # reason — never a raw traceback reaching the user.
    monkeypatch.setenv("DATABASE_URL", "not-a-valid-url")

    from app import config

    config.get_settings.cache_clear()

    from app import main as main_module

    with pytest.raises(SystemExit):
        importlib.reload(main_module)

    config.get_settings.cache_clear()
