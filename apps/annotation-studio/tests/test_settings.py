import pytest
from pydantic import ValidationError

from annotation_studio.settings import AppSettings, SourceSettings


def test_source_settings_reads_separate_tokens(monkeypatch) -> None:
    monkeypatch.setenv("RX_ASSISTANT_LOGFIRE_READ_TOKEN", "pylf_read_test")
    monkeypatch.setenv("RX_ASSISTANT_LOGFIRE_WRITE_TOKEN", "pylf_write_test")

    settings = SourceSettings()

    assert settings.read_token == "pylf_read_test"
    assert settings.write_token == "pylf_write_test"
    assert settings.top_level_agent_name == "rx_assistant_agent"


@pytest.mark.parametrize("missing_var", ["RX_ASSISTANT_LOGFIRE_READ_TOKEN", "RX_ASSISTANT_LOGFIRE_WRITE_TOKEN"])
def test_source_settings_requires_both_tokens(monkeypatch, missing_var: str) -> None:
    monkeypatch.setenv("RX_ASSISTANT_LOGFIRE_READ_TOKEN", "read")
    monkeypatch.setenv("RX_ASSISTANT_LOGFIRE_WRITE_TOKEN", "write")
    monkeypatch.delenv(missing_var, raising=False)

    with pytest.raises(ValidationError):
        SourceSettings()


def test_app_settings_default(monkeypatch) -> None:
    monkeypatch.delenv("ANNOTATION_STUDIO_DATABASE_PATH", raising=False)

    assert AppSettings().database_path == "data/annotation_studio.sqlite3"


def test_app_settings_reads_override(monkeypatch) -> None:
    monkeypatch.setenv("ANNOTATION_STUDIO_DATABASE_PATH", "/tmp/x.sqlite3")

    assert AppSettings().database_path == "/tmp/x.sqlite3"
