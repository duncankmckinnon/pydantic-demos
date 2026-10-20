from __future__ import annotations

import hashlib
from typing import Any, Literal

import logfire
from pydantic import BaseModel, ConfigDict


class AgentConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    api_format: Literal["anthropic", "openai-responses"]
    model_name: str

    @property
    def model_choice(self) -> tuple[str, str]:
        return self.api_format, self.model_name


DEFAULT_PROMPT = (
    "You are an HR representative. Answer the applicant's questions and use "
    "the available tools when helpful."
)
DEFAULT_MODEL = AgentConfig(api_format="anthropic", model_name="claude-sonnet-5")

# The current choices remain valid during the transition. The smaller candidates are
# the intended experiment pair and will be verified against the project's Gateway routes.
ALLOWED_MODELS = frozenset(
    {
        DEFAULT_MODEL.model_choice,
        ("openai-responses", "gpt-5.6"),
        ("openai-responses", "gpt-5.6-luna"),
        ("openai-responses", "gpt-5.4-mini"),
        ("anthropic", "claude-haiku-5-5"),
    }
)

PROMPT_VARIABLE = logfire.var(
    "applicant_support_prompt",
    type=str,
    default=DEFAULT_PROMPT,
    description="System prompt used for new applicant-support chat sessions.",
)
MODEL_VARIABLE = logfire.var(
    "applicant_support_model",
    type=AgentConfig,
    default=DEFAULT_MODEL,
    description="Pydantic AI Gateway model used for new applicant-support chat sessions.",
)


class ResolvedAgentConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt: str
    model: AgentConfig
    prompt_fingerprint: str
    prompt_source: str
    model_source: str

    def trace_metadata(self) -> dict[str, str]:
        return {
            "resolved_model": ":".join(self.model.model_choice),
            "prompt_fingerprint": self.prompt_fingerprint,
            "prompt_source": self.prompt_source,
            "model_source": self.model_source,
        }


def _resolved_value(value: Any) -> Any:
    """Accept both Logfire's resolution wrapper and small test doubles."""
    return getattr(value, "value", value)


def _resolution_source(value: Any) -> str:
    reason = getattr(value, "reason", None)
    if reason in (None, "resolved"):
        return "managed"
    if reason == "context_override":
        return "context_override"
    if reason == "code_default":
        return "default_code"
    return f"default_{reason}"


def _resolve_prompt() -> tuple[str, str]:
    try:
        with PROMPT_VARIABLE.get() as resolved:
            prompt = _resolved_value(resolved)
            source = _resolution_source(resolved)
        if isinstance(prompt, str) and prompt.strip():
            return prompt, source
    except Exception:
        pass
    return DEFAULT_PROMPT, "default_resolution_error"


def _resolve_model() -> tuple[AgentConfig, str]:
    try:
        with MODEL_VARIABLE.get() as resolved:
            model = AgentConfig.model_validate(_resolved_value(resolved))
            source = _resolution_source(resolved)
    except Exception:
        return DEFAULT_MODEL, "default_resolution_error"

    if model.model_choice not in ALLOWED_MODELS:
        return DEFAULT_MODEL, "default_invalid_managed_value"
    return model, source


def resolve_agent_config() -> ResolvedAgentConfig:
    prompt, prompt_source = _resolve_prompt()
    model, model_source = _resolve_model()
    prompt_fingerprint = hashlib.sha256(prompt.encode()).hexdigest()[:12]
    return ResolvedAgentConfig(
        prompt=prompt,
        model=model,
        prompt_fingerprint=prompt_fingerprint,
        prompt_source=prompt_source,
        model_source=model_source,
    )
