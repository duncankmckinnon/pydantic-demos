import pytest

from demo_core.settings import GatewaySettings, LogfireSettings


def test_gateway_settings_reads_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_API_KEY", "pylf_v_test")
    settings = GatewaySettings()
    assert settings.api_key == "pylf_v_test"


def test_gateway_settings_accepts_explicit_kwarg() -> None:
    settings = GatewaySettings(api_key="explicit-key")
    assert settings.api_key == "explicit-key"


def test_gateway_settings_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYDANTIC_AI_GATEWAY_API_KEY", raising=False)
    with pytest.raises(Exception):
        GatewaySettings(_env_file=None)


def test_logfire_settings_reads_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOGFIRE_TOKEN", "test-token")
    assert LogfireSettings().token == "test-token"
    assert LogfireSettings(token="explicit-token").token == "explicit-token"
