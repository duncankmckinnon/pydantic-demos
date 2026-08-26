from dataclasses import dataclass

import pytest
from pydantic_ai import Agent, ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from demo_core.evals import HarnessJudge


@dataclass
class _FakeCtx:
    """Stands in for pydantic_evals.evaluators.EvaluatorContext in this unit test.

    HarnessJudge.evaluate only reads ctx.output, so a minimal double is enough
    to test its logic without depending on EvaluatorContext's real constructor.
    """

    output: str


def _fixed_score_model(score: str):
    def respond(messages, info):
        return ModelResponse(parts=[TextPart(content=score)])

    return FunctionModel(respond)


@pytest.mark.asyncio
async def test_harness_judge_parses_float_from_agent_reply() -> None:
    judge_agent = Agent(_fixed_score_model("0.85"), name="test_judge")
    judge = HarnessJudge(agent=judge_agent, rubric="Score the output from 0 to 1.")

    score = await judge.evaluate(_FakeCtx(output="the thing being judged"))

    assert score == 0.85


@pytest.mark.asyncio
async def test_harness_judge_includes_rubric_and_output_in_prompt() -> None:
    seen_prompts: list[str] = []

    def respond(messages, info):
        seen_prompts.append(messages[-1].parts[-1].content)
        return ModelResponse(parts=[TextPart(content="1.0")])

    judge_agent = Agent(FunctionModel(respond), name="test_judge")
    judge = HarnessJudge(agent=judge_agent, rubric="Is this polite?")

    await judge.evaluate(_FakeCtx(output="hello there"))

    assert "Is this polite?" in seen_prompts[0]
    assert "hello there" in seen_prompts[0]
