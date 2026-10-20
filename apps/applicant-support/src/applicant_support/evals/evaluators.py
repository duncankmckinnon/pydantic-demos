from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

from applicant_support.evals.models import ApplicantSupportEvalOutput
from demo_core.models import get_model
from demo_core.settings import GatewaySettings

JUDGE_MODEL = ("anthropic", "claude-sonnet-5")
ToolUseCategory = Literal["appropriate", "unnecessary", "missed_opportunity"]


class ToolUseJudgment(BaseModel):
    category: ToolUseCategory
    explanation: str


class ScoreJudgment(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    explanation: str


def _judge_agent(name: str, output_type: type[BaseModel], instructions: str) -> Agent:
    return Agent(
        get_model(*JUDGE_MODEL, GatewaySettings()),
        name=name,
        output_type=output_type,
        instructions=instructions,
    )


_TOOL_USE_JUDGE = _judge_agent(
    "applicant_support_tool_use_judge",
    ToolUseJudgment,
    "Judge whether the applicant-support agent used its tools appropriately. Available "
    "tools are get_application_status for applicant facts, reschedule_screening_call for "
    "moving an existing screening call, and search_roles for open or closed role facts. "
    "Return appropriate, unnecessary, or missed_opportunity with a concise explanation.",
)
_GROUNDEDNESS_JUDGE = _judge_agent(
    "applicant_support_groundedness_judge",
    ScoreJudgment,
    "Score from 0 to 1 whether the reply is supported by the supplied tool evidence and "
    "expected outcome, avoids invented facts, and does not contradict applicant records.",
)
_PROFESSIONALISM_JUDGE = _judge_agent(
    "applicant_support_professionalism_judge",
    ScoreJudgment,
    "Score from 0 to 1 whether the reply is clear, respectful, appropriately bounded, and "
    "suitable for an HR applicant interaction.",
)
_FRUSTRATION_JUDGE = _judge_agent(
    "applicant_support_frustration_judge",
    ScoreJudgment,
    "Score from 0 to 1 whether the reply acknowledges and productively addresses any "
    "applicant frustration without being dismissive or escalating it. A neutral question "
    "with a helpful neutral answer may receive a full score.",
)


def _normalize_reply(output: Any) -> str:
    if isinstance(output, ApplicantSupportEvalOutput):
        return output.reply
    if isinstance(output, dict) and isinstance(output.get("reply"), str):
        return output["reply"]
    return str(output)


def _question_text(inputs: Any) -> str:
    question = getattr(inputs, "question", None)
    if isinstance(question, str):
        return question
    if isinstance(inputs, dict) and isinstance(inputs.get("question"), str):
        return inputs["question"]
    return str(inputs)


def _called_tool_names(ctx: EvaluatorContext[Any, Any]) -> list[str]:
    names = {
        span.name.removeprefix("execute_tool ")
        for span in ctx.span_tree.find(
            lambda node: node.name.startswith("execute_tool ")
        )
    }
    return sorted(names)


def _tool_return_evidence(ctx: EvaluatorContext[Any, Any]) -> list[str]:
    evidence: list[str] = []
    for span in ctx.span_tree.find(
        lambda node: node.name.startswith("execute_tool ")
    ):
        result = span.attributes.get("gen_ai.tool.call.result")
        if result is None:
            result = span.attributes.get("tool_response")
        if result is None:
            continue
        if not isinstance(result, str):
            result = json.dumps(result, default=str, sort_keys=True)
        evidence.append(f"{span.name.removeprefix('execute_tool ')}: {result}")
    return evidence


def _expected_text(expected_output: Any) -> str:
    if expected_output is None:
        return "none supplied"
    return _normalize_reply(expected_output)


@dataclass
class ReplyPresent(Evaluator[Any, Any]):
    def evaluate(self, ctx: EvaluatorContext[Any, Any]) -> bool:
        return bool(_normalize_reply(ctx.output).strip())


@dataclass
class ToolUseAppropriateness(Evaluator[Any, Any]):
    agent: Agent = field(default_factory=lambda: _TOOL_USE_JUDGE)

    async def evaluate(self, ctx: EvaluatorContext[Any, Any]) -> EvaluationReason:
        question = _question_text(ctx.inputs)
        tools = _called_tool_names(ctx)
        lookup_question = any(
            phrase in question.lower()
            for phrase in ("application", "screening", "my status", "interview")
        )
        if lookup_question and "get_application_status" not in tools:
            return EvaluationReason(
                value="missed_opportunity",
                reason=(
                    "Application facts were requested without calling "
                    "get_application_status."
                ),
            )

        result = await self.agent.run(
            f"Applicant question:\n{question}\n\n"
            f"Agent reply:\n{_normalize_reply(ctx.output)}\n\n"
            f"Tools called: {', '.join(tools) if tools else 'none'}."
        )
        return EvaluationReason(
            value=result.output.category,
            reason=result.output.explanation,
        )


@dataclass
class GroundedCorrectness(Evaluator[Any, Any]):
    agent: Agent = field(default_factory=lambda: _GROUNDEDNESS_JUDGE)

    async def evaluate(self, ctx: EvaluatorContext[Any, Any]) -> EvaluationReason:
        evidence = _tool_return_evidence(ctx)
        result = await self.agent.run(
            f"Applicant question:\n{_question_text(ctx.inputs)}\n\n"
            f"Agent reply:\n{_normalize_reply(ctx.output)}\n\n"
            f"Expected outcome:\n{_expected_text(ctx.expected_output)}\n\n"
            f"Actual tool returns:\n{chr(10).join(evidence) if evidence else 'none'}"
        )
        return EvaluationReason(
            value=result.output.score,
            reason=result.output.explanation,
        )


@dataclass
class Professionalism(Evaluator[Any, Any]):
    agent: Agent = field(default_factory=lambda: _PROFESSIONALISM_JUDGE)

    async def evaluate(self, ctx: EvaluatorContext[Any, Any]) -> EvaluationReason:
        result = await self.agent.run(
            f"Applicant question:\n{_question_text(ctx.inputs)}\n\n"
            f"Agent reply:\n{_normalize_reply(ctx.output)}"
        )
        return EvaluationReason(
            value=result.output.score,
            reason=result.output.explanation,
        )


@dataclass
class FrustrationHandling(Evaluator[Any, Any]):
    agent: Agent = field(default_factory=lambda: _FRUSTRATION_JUDGE)

    async def evaluate(self, ctx: EvaluatorContext[Any, Any]) -> EvaluationReason:
        result = await self.agent.run(
            f"Applicant question:\n{_question_text(ctx.inputs)}\n\n"
            f"Agent reply:\n{_normalize_reply(ctx.output)}"
        )
        return EvaluationReason(
            value=result.output.score,
            reason=result.output.explanation,
        )


reply_present = ReplyPresent()
tool_use_appropriateness = ToolUseAppropriateness()
grounded_correctness = GroundedCorrectness()
professionalism = Professionalism()
frustration_handling = FrustrationHandling()

SHARED_EVALUATORS = [
    reply_present,
    tool_use_appropriateness,
    grounded_correctness,
    professionalism,
    frustration_handling,
]
