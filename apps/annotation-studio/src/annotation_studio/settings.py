from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SourceSettings(BaseSettings):
    """Read spans and append annotation events in the configured Logfire project."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    read_token: str = Field(validation_alias="LOGFIRE_READ_TOKEN")
    write_token: str = Field(validation_alias="LOGFIRE_WRITE_TOKEN")
    datasets_token: str = Field(validation_alias="LOGFIRE_DATASETS_TOKEN")


class AppSettings(BaseSettings):
    """This app's own local settings — its SQLite database."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    database_path: str = Field(
        default="data/annotation_studio.sqlite3", validation_alias="ANNOTATION_STUDIO_DATABASE_PATH"
    )
