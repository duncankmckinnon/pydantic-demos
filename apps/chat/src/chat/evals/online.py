from dataclasses import dataclass
from typing import Any

from pydantic_evals.evaluators import Evaluator, EvaluatorContext
from pydantic_evals.online import OnlineEvaluator
from pydantic_evals.online_capability import OnlineEvaluation

from chat.evals.dataset import chat_quality_judge
from chat.evals.efficiency import chat_efficiency_judge
from chat.evals.tool_use import chat_tool_use_judge


@dataclass
class ReplyNotEmpty(Evaluator[Any, Any]):
    """Free structural check, so it runs on every call rather than being sampled."""

    def evaluate(self, ctx: EvaluatorContext[Any, Any]) -> bool:
        return bool(str(ctx.output).strip())


# Attached to the chat agent (see chat.agent.build_agent) so every real `/api/chat` call is
# scored in the background; results show up as gen_ai.evaluation.result events in Logfire's
# Live Evaluations view. chat_quality_judge, chat_efficiency_judge, and chat_tool_use_judge
# each make a real model call, so they're sampled at 20% rather than run on every message;
# ReplyNotEmpty is free and runs on all of them.
CHAT_ONLINE_EVALUATION = OnlineEvaluation(
    evaluators=[
        ReplyNotEmpty(),
        # OnlineEvaluator(evaluator=chat_quality_judge, sample_rate=0.2),
        OnlineEvaluator(evaluator=chat_efficiency_judge, sample_rate=0.2),
        OnlineEvaluator(evaluator=chat_tool_use_judge, sample_rate=0.2),
    ]
)
