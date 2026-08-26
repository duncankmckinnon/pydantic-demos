from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    """Pydantic AI Gateway credentials, loaded from the current app's environment."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    api_key: str = Field(validation_alias="PYDANTIC_AI_GATEWAY_API_KEY")


class LogfireSettings(BaseSettings):
    """Logfire credentials, loaded from the current app's environment."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    token: str = Field(validation_alias="LOGFIRE_TOKEN")
