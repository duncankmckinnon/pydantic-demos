from dataclasses import dataclass
from typing import Any

from pydantic_evals.evaluators import Evaluator, EvaluatorContext
from pydantic_evals.online import OnlineEvaluator
from pydantic_evals.online_capability import OnlineEvaluation

from chat.evals.dataset import chat_quality_judge


@dataclass
class ReplyNotEmpty(Evaluator[Any, Any]):
    """Free structural check, so it runs on every call rather than being sampled."""

    def evaluate(self, ctx: EvaluatorContext[Any, Any]) -> bool:
        return bool(str(ctx.output).strip())


# Attached to the chat agent (see chat.agent.build_agent) so every real `/api/chat` call is
# scored in the background; results show up as gen_ai.evaluation.result events in Logfire's
# Live Evaluations view. chat_quality_judge makes a real model call, so it's sampled at 20%
# rather than run on every message; ReplyNotEmpty is free and runs on all of them.
CHAT_ONLINE_EVALUATION = OnlineEvaluation(
    evaluators=[
        ReplyNotEmpty(),
        OnlineEvaluator(evaluator=chat_quality_judge, sample_rate=0.2),
    ]
)
