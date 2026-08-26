from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic_ai import Agent
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from chat.agent import MODEL_CHOICES
from demo_core.models import get_model
from demo_core.settings import GatewaySettings

EfficiencyRating = Literal["efficient", "questionable", "inefficient"]

# Reuses chat.agent's own default model choice for the same reason chat.evals.dataset's judge
# does — so the two can't silently drift out of sync.
_efficiency_judge_agent = Agent(
    get_model(*MODEL_CHOICES[0], GatewaySettings()),
    name="chat_efficiency_judge",
    output_type=EfficiencyRating,
    instructions=(
        "You rate the token efficiency of a chatbot response given the user's message, the "
        "reply, and how many input/output tokens the model used to produce it. Judge cost "
        "against complexity, not an absolute token count: a long reply to a genuinely complex "
        "question can be efficient, while a bloated reply to a simple one is inefficient. "
        "Reply with exactly one of: efficient, questionable, inefficient."
    ),
)


def _sum_token_usage(ctx: EvaluatorContext[Any, Any]) -> tuple[int, int]:
    """Sum gen_ai.usage.{input,output}_tokens across every span in the run.

    Pydantic AI's Logfire instrumentation tags each model-call span with these attributes
    already, and (by default) reports the agent run's own aggregate under
    gen_ai.aggregated_usage.* instead, specifically so summing gen_ai.usage.* across all spans
    in a trace doesn't double-count against that aggregate. So this sums every matching span
    with no risk of double-counting, and needs no cooperation from the calling task function —
    works the same for both the offline Dataset and the online capability.
    """
    input_tokens = 0
    output_tokens = 0
    for span in ctx.span_tree.find(lambda node: "gen_ai.usage.input_tokens" in node.attributes):
        input_tokens += int(span.attributes.get("gen_ai.usage.input_tokens", 0))
        output_tokens += int(span.attributes.get("gen_ai.usage.output_tokens", 0))
    return input_tokens, output_tokens


@dataclass
class ResponseEfficiency(Evaluator[Any, Any]):
    """Scores a reply's token efficiency by delegating judgment to an Agent, given the
    input, output, and the run's own token usage (read from its span tree)."""

    agent: Agent = field(default_factory=lambda: _efficiency_judge_agent)

    async def evaluate(self, ctx: EvaluatorContext[Any, Any]) -> EfficiencyRating:
        input_tokens, output_tokens = _sum_token_usage(ctx)
        prompt = (
            f"User message:\n{ctx.inputs}\n\n"
            f"Reply:\n{ctx.output}\n\n"
            f"Tokens used — input: {input_tokens}, output: {output_tokens}, "
            f"total: {input_tokens + output_tokens}."
        )
        result = await self.agent.run(prompt)
        return result.output


# Named instance (like chat.evals.dataset.chat_quality_judge) so the offline dataset and the
# online capability share one evaluator instead of two copies that can drift apart.
chat_efficiency_judge = ResponseEfficiency()
