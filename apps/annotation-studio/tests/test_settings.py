import pytest
from pydantic import ValidationError

from annotation_studio.settings import AppSettings, SourceSettings


def test_source_settings_reads_separate_tokens(monkeypatch) -> None:
    monkeypatch.setenv("LOGFIRE_READ_TOKEN", "pylf_read_test")
    monkeypatch.setenv("LOGFIRE_WRITE_TOKEN", "pylf_write_test")
    monkeypatch.setenv("LOGFIRE_DATASETS_TOKEN", "pylf_datasets_test")

    settings = SourceSettings()

    assert settings.read_token == "pylf_read_test"
    assert settings.write_token == "pylf_write_test"
    assert settings.datasets_token == "pylf_datasets_test"


@pytest.mark.parametrize(
    "missing_var",
    ["LOGFIRE_READ_TOKEN", "LOGFIRE_WRITE_TOKEN", "LOGFIRE_DATASETS_TOKEN"],
)
def test_source_settings_requires_both_tokens(monkeypatch, missing_var: str) -> None:
    monkeypatch.setenv("LOGFIRE_READ_TOKEN", "read")
    monkeypatch.setenv("LOGFIRE_WRITE_TOKEN", "write")
    monkeypatch.delenv(missing_var, raising=False)

    with pytest.raises(ValidationError):
        SourceSettings()


def test_app_settings_default(monkeypatch) -> None:
    monkeypatch.delenv("ANNOTATION_STUDIO_DATABASE_PATH", raising=False)

    assert AppSettings().database_path == "data/annotation_studio.sqlite3"


def test_app_settings_reads_override(monkeypatch) -> None:
    monkeypatch.setenv("ANNOTATION_STUDIO_DATABASE_PATH", "/tmp/x.sqlite3")

    assert AppSettings().database_path == "/tmp/x.sqlite3"
