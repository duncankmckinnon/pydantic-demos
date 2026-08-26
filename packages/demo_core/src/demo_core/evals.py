from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent
from pydantic_evals.evaluators import Evaluator, EvaluatorContext


@dataclass
class HarnessJudge(Evaluator[Any, Any]):
    """Scores a pydantic-evals Case's output by delegating judgment to an Agent.

    `agent` can be a plain Agent for a straightforward rubric, or a
    pydantic-ai-harness-equipped Agent (e.g. with Shell/CodeMode to execute and
    check generated code, or SubAgents to decompose a complex rubric) when the
    judgment itself needs more than a single model call.
    """

    agent: Agent
    rubric: str

    async def evaluate(self, ctx: EvaluatorContext[Any, Any]) -> float:
        result = await self.agent.run(f"{self.rubric}\n\nOutput to judge:\n{ctx.output}")
        return float(result.output)
