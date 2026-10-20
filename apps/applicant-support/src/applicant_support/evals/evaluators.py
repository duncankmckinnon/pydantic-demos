from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from functools import lru_cache
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


@lru_cache(maxsize=1)
def _get_tool_use_judge() -> Agent:
    return _judge_agent(
        "applicant_support_tool_use_judge",
        ToolUseJudgment,
        "Judge whether the applicant-support agent used its tools appropriately. Available "
        "tools are get_application_status for applicant facts, reschedule_screening_call for "
        "moving an existing screening call, and search_roles for open or closed role facts. "
        "Return appropriate, unnecessary, or missed_opportunity with a concise explanation.",
    )


@lru_cache(maxsize=1)
def _get_groundedness_judge() -> Agent:
    return _judge_agent(
        "applicant_support_groundedness_judge",
        ScoreJudgment,
        "Score from 0 to 1 whether the reply is supported by the supplied tool evidence and "
        "expected outcome, avoids invented facts, and does not contradict applicant records.",
    )


@lru_cache(maxsize=1)
def _get_professionalism_judge() -> Agent:
    return _judge_agent(
        "applicant_support_professionalism_judge",
        ScoreJudgment,
        "Score from 0 to 1 whether the reply is clear, respectful, appropriately bounded, and "
        "suitable for an HR applicant interaction.",
    )


@lru_cache(maxsize=1)
def _get_frustration_judge() -> Agent:
    return _judge_agent(
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


def _required_tool(
    ctx: EvaluatorContext[Any, Any],
) -> tuple[bool, str | None]:
    metadata = ctx.metadata
    if isinstance(metadata, dict):
        if "required_tool" not in metadata:
            return False, None
        value = metadata.get("required_tool")
    elif hasattr(metadata, "required_tool"):
        value = getattr(metadata, "required_tool", None)
    else:
        return False, None
    return True, value if isinstance(value, str) and value else None


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
    agent: Agent | None = None

    async def evaluate(self, ctx: EvaluatorContext[Any, Any]) -> EvaluationReason:
        question = _question_text(ctx.inputs)
        tools = _called_tool_names(ctx)
        has_required_tool_metadata, required_tool = _required_tool(ctx)
        if required_tool is not None and required_tool not in tools:
            return EvaluationReason(
                value="missed_opportunity",
                reason=(
                    f"The case required {required_tool}, but that tool was not called."
                ),
            )
        lookup_question = not has_required_tool_metadata and any(
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

        agent = self.agent or _get_tool_use_judge()
        result = await agent.run(
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
    agent: Agent | None = None

    async def evaluate(self, ctx: EvaluatorContext[Any, Any]) -> EvaluationReason:
        evidence = _tool_return_evidence(ctx)
        agent = self.agent or _get_groundedness_judge()
        result = await agent.run(
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
    agent: Agent | None = None

    async def evaluate(self, ctx: EvaluatorContext[Any, Any]) -> EvaluationReason:
        agent = self.agent or _get_professionalism_judge()
        result = await agent.run(
            f"Applicant question:\n{_question_text(ctx.inputs)}\n\n"
            f"Agent reply:\n{_normalize_reply(ctx.output)}"
        )
        return EvaluationReason(
            value=result.output.score,
            reason=result.output.explanation,
        )


@dataclass
class FrustrationHandling(Evaluator[Any, Any]):
    agent: Agent | None = None

    async def evaluate(self, ctx: EvaluatorContext[Any, Any]) -> EvaluationReason:
        agent = self.agent or _get_frustration_judge()
        result = await agent.run(
            f"Applicant question:\n{_question_text(ctx.inputs)}\n\n"
            f"Agent reply:\n{_normalize_reply(ctx.output)}"
        )
        return EvaluationReason(
            value=result.output.score,
            reason=result.output.explanation,
        )


@dataclass
class ApplicantSupportQuality(Evaluator[Any, Any]):
    reply_present: ReplyPresent = field(default_factory=ReplyPresent)
    tool_use: ToolUseAppropriateness = field(default_factory=ToolUseAppropriateness)
    grounded_correctness: GroundedCorrectness = field(
        default_factory=GroundedCorrectness
    )
    professionalism: Professionalism = field(default_factory=Professionalism)
    frustration_handling: FrustrationHandling = field(
        default_factory=FrustrationHandling
    )

    async def evaluate(
        self, ctx: EvaluatorContext[Any, Any]
    ) -> dict[str, EvaluationReason]:
        reply_score = float(self.reply_present.evaluate(ctx))
        tool_use, grounded, professionalism, frustration = await asyncio.gather(
            self.tool_use.evaluate(ctx),
            self.grounded_correctness.evaluate(ctx),
            self.professionalism.evaluate(ctx),
            self.frustration_handling.evaluate(ctx),
        )
        tool_score = 1.0 if tool_use.value == "appropriate" else 0.0
        quality_scores = {
            "ToolUseScore": tool_score,
            "GroundedCorrectness": float(grounded.value),
            "Professionalism": float(professionalism.value),
            "FrustrationHandling": float(frustration.value),
        }
        overall_score = sum(quality_scores.values()) / len(quality_scores)
        component_summary = ", ".join(
            f"{name}={score:.2f}" for name, score in quality_scores.items()
        )

        return {
            "OverallQuality": EvaluationReason(
                value=overall_score,
                reason=f"Equal-weight normalized average: {component_summary}.",
            ),
            "ReplyPresent": EvaluationReason(
                value=reply_score,
                reason="The agent returned a non-empty reply."
                if reply_score
                else "The agent returned an empty reply.",
            ),
            "ToolUseAppropriateness": tool_use,
            "ToolUseScore": EvaluationReason(
                value=tool_score,
                reason=tool_use.reason,
            ),
            "GroundedCorrectness": grounded,
            "Professionalism": professionalism,
            "FrustrationHandling": frustration,
        }


applicant_support_quality = ApplicantSupportQuality()
reply_present = applicant_support_quality.reply_present
tool_use_appropriateness = applicant_support_quality.tool_use
grounded_correctness = applicant_support_quality.grounded_correctness
professionalism = applicant_support_quality.professionalism
frustration_handling = applicant_support_quality.frustration_handling

SHARED_EVALUATORS = [
    applicant_support_quality,
]
