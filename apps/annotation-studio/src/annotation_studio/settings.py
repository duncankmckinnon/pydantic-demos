from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SourceSettings(BaseSettings):
    """Read spans and append annotation events in rx-assistant's Logfire project."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    read_token: str = Field(validation_alias="RX_ASSISTANT_LOGFIRE_READ_TOKEN")
    write_token: str = Field(validation_alias="RX_ASSISTANT_LOGFIRE_WRITE_TOKEN")
    top_level_agent_name: str = Field(default="rx_assistant_agent")


class AppSettings(BaseSettings):
    """This app's own local settings — its SQLite database."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    database_path: str = Field(
        default="data/annotation_studio.sqlite3", validation_alias="ANNOTATION_STUDIO_DATABASE_PATH"
    )
