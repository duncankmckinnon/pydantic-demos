from contextlib import contextmanager
from typing import Any

import pytest

from applicant_support.config import (
    DEFAULT_MODEL,
    AgentConfig,
    resolve_agent_config,
)


class FakeVariable:
    def __init__(self, value: Any) -> None:
        self.value = value

    @contextmanager
    def get(self):
        yield self.value


class FakeResolution:
    def __init__(self, value: Any, reason: str) -> None:
        self.value = value
        self.reason = reason


def test_resolve_agent_config_uses_managed_values_and_safe_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import applicant_support.config as config_module

    prompt = "Always look up application facts before answering."
    model = {"api_format": "openai-responses", "model_name": "gpt-5.4-mini"}
    monkeypatch.setattr(config_module, "PROMPT_VARIABLE", FakeVariable(prompt))
    monkeypatch.setattr(config_module, "MODEL_VARIABLE", FakeVariable(model))

    first = resolve_agent_config()
    second = resolve_agent_config()

    assert first.prompt == prompt
    assert first.model == AgentConfig.model_validate(model)
    assert first.prompt_source == "managed"
    assert first.model_source == "managed"
    assert first.prompt_fingerprint == second.prompt_fingerprint
    assert len(first.prompt_fingerprint) == 12
    assert prompt not in repr(first.trace_metadata())
    assert first.trace_metadata() == {
        "resolved_model": "openai-responses:gpt-5.4-mini",
        "prompt_fingerprint": first.prompt_fingerprint,
        "prompt_source": "managed",
        "model_source": "managed",
    }


def test_resolve_agent_config_accepts_gpt_5_6_luna_rollout_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import applicant_support.config as config_module

    managed_model = {
        "api_format": "openai-responses",
        "model_name": "gpt-5.6-luna",
    }
    monkeypatch.setattr(config_module, "PROMPT_VARIABLE", FakeVariable("Managed prompt"))
    monkeypatch.setattr(config_module, "MODEL_VARIABLE", FakeVariable(managed_model))

    resolved = resolve_agent_config()

    assert resolved.model == AgentConfig.model_validate(managed_model)
    assert resolved.model_source == "managed"


def test_resolve_agent_config_rejects_non_allowlisted_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import applicant_support.config as config_module

    monkeypatch.setattr(config_module, "PROMPT_VARIABLE", FakeVariable("Managed prompt"))
    monkeypatch.setattr(
        config_module,
        "MODEL_VARIABLE",
        FakeVariable({"api_format": "anthropic", "model_name": "unknown-model"}),
    )

    resolved = resolve_agent_config()

    assert resolved.model == DEFAULT_MODEL
    assert resolved.model_source == "default_invalid_managed_value"


def test_resolve_agent_config_labels_code_defaults_as_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import applicant_support.config as config_module

    monkeypatch.setattr(
        config_module,
        "PROMPT_VARIABLE",
        FakeVariable(FakeResolution("Default prompt", "code_default")),
    )
    monkeypatch.setattr(
        config_module,
        "MODEL_VARIABLE",
        FakeVariable(FakeResolution(DEFAULT_MODEL, "no_provider")),
    )

    resolved = resolve_agent_config()

    assert resolved.prompt_source == "default_code"
    assert resolved.model_source == "default_no_provider"
