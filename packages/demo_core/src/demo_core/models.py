from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.gateway import gateway_provider

from demo_core.settings import GatewaySettings

_MODEL_CLASSES: dict[str, type[Model]] = {
    "openai": OpenAIChatModel,
    # Distinct from "openai": OpenAIChatModel (Chat Completions) doesn't support native
    # WebSearchTool for general models — only OpenAI's dedicated "-search-preview" model
    # variants do. OpenAIResponsesModel (Responses API) supports it for any model.
    "openai-responses": OpenAIResponsesModel,
    "anthropic": AnthropicModel,
}


def get_model(api_format: str, model_name: str, settings: GatewaySettings) -> Model:
    """Build a pydantic-ai Model routed through the Pydantic AI Gateway.

    `api_format` selects both the Gateway routing prefix and the pydantic-ai
    model class to construct (e.g. "openai", "anthropic").
    """
    model_cls = _MODEL_CLASSES.get(api_format)
    if model_cls is None:
        raise ValueError(
            f"Unsupported api_format: {api_format!r}. "
            f"Supported: {sorted(_MODEL_CLASSES)}"
        )
    provider = gateway_provider(api_format, api_key=settings.api_key)
    return model_cls(model_name, provider=provider)
