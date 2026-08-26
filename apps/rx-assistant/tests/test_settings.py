from rx_assistant.settings import DatabaseSettings


def test_database_settings_reads_database_url(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")

    settings = DatabaseSettings()

    assert settings.database_url == "postgresql://u:p@host:5432/db"
