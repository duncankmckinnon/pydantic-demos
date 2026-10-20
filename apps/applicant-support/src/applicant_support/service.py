from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic_ai import Agent, AgentRunResult
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model

from applicant_support.agent import AgentDeps
from applicant_support.config import ResolvedAgentConfig


async def run_applicant_turn(
    agent: Agent[AgentDeps, str],
    *,
    question: str,
    deps: AgentDeps,
    config: ResolvedAgentConfig,
    history: Sequence[ModelMessage] = (),
    metadata: Mapping[str, Any] | None = None,
    model: Model | None = None,
) -> AgentRunResult[str]:
    run_metadata = dict(metadata or {})
    for unsafe_prompt_key in ("prompt", "system_prompt", "instructions"):
        run_metadata.pop(unsafe_prompt_key, None)
    run_metadata.update(config.trace_metadata())
    run_metadata["application_id"] = deps.application_id

    return await agent.run(
        question,
        deps=deps,
        model=model,
        instructions=config.prompt,
        message_history=history,
        metadata=run_metadata,
    )
