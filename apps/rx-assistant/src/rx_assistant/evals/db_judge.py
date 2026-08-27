from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

from demo_core.models import get_model
from demo_core.settings import GatewaySettings
from rx_assistant.agent import Deps, MODEL_CHOICES
from rx_assistant.db import create_pool, query_medications
from rx_assistant.embeddings import encode_text, load_embedding_model
from rx_assistant.settings import DatabaseSettings

ToolUseCategory = Literal["appropriate", "unnecessary", "missed_opportunity"]
TrajectoryCategory = Literal["efficient", "reasonable", "inefficient"]


class RxAssistantJudgment(BaseModel):
    grounded: bool
    grounding_explanation: str
    tool_use: ToolUseCategory
    tool_use_explanation: str
    trajectory: TrajectoryCategory
    trajectory_explanation: str


# Reuses rx_assistant.agent's own default model choice for the same reason chat's judges do —
# so this judge and the live agent can't silently drift out of sync.
_judge_agent = Agent(
    get_model(*MODEL_CHOICES[0], GatewaySettings()),
    name="rx_assistant_db_judge",
    deps_type=Deps,
    output_type=RxAssistantJudgment,
    instructions=(
        "You judge a pharmacy assistant's reply to a patient question, with your own "
        "search_medications tool over the same database the assistant used. Use it to "
        "independently check any medication or condition the reply mentions. You'll be told "
        "the assistant's own tool calls in order. Judge three things: (1) grounded — true if "
        "every medication/condition fact in the reply is consistent with what you find in "
        "the database (or the reply made no such factual claims), false if it appears to "
        "reference something not in the database; (2) tool_use — 'appropriate' if the "
        "assistant's decision to search (or not search), and what it searched for, fit the "
        "question asked, 'unnecessary' if it searched but didn't need to, or "
        "'missed_opportunity' if it should have searched but didn't; (3) trajectory — "
        "'efficient' if the assistant reached its answer via a sensible, minimal sequence of "
        "steps, 'reasonable' if the path was a bit roundabout but still justified, or "
        "'inefficient' if it repeated calls, delegated to web_research without first "
        "checking the database, or took clearly more steps than the question needed. Always "
        "give a short explanation for each."
    ),
)


@_judge_agent.tool
async def search_medications(ctx: RunContext[Deps], query: str, condition: str | None = None, limit: int = 5):
    """Find medications matching a natural-language query, optionally scoped to a condition
    name."""
    embedding = await encode_text(ctx.deps.embedding_model, query)
    return await query_medications(ctx.deps.pool, embedding, condition, limit)


def _tool_call_sequence(ctx: EvaluatorContext[Any, Any]) -> list[str]:
    """Return the tool names the *assistant* (not this judge) invoked during the run being
    judged, in call order — read from its execute_tool spans, sorted by start_timestamp.
    Unlike a deduped/sorted set, this preserves repeats and ordering, which trajectory
    judgment needs to see the actual path taken. rx-assistant has no native/builtin tools
    (unlike chat's web_search), so unlike chat.evals.tool_use._called_tool_names, there's no
    gen_ai.output.messages branch to check."""
    spans = sorted(
        ctx.span_tree.find(lambda node: node.name.startswith("execute_tool ")),
        key=lambda node: node.start_timestamp,
    )
    return [span.name.removeprefix("execute_tool ") for span in spans]


async def _get_judge_deps() -> Deps:
    """Lazily creates and reuses one small pool + embedding model for this judge's own tool
    calls, independent of the live app's Deps — the judge runs as an online-eval capability,
    sampled at a low rate, and needs no coordination with the running app's lifecycle."""
    if _judge_deps_holder.deps is None:
        database_settings = DatabaseSettings()
        pool = await create_pool(database_settings.database_url)
        _judge_deps_holder.deps = Deps(pool=pool, embedding_model=load_embedding_model())
    return _judge_deps_holder.deps


@dataclass
class _JudgeDepsHolder:
    deps: Deps | None = None


_judge_deps_holder = _JudgeDepsHolder()


@dataclass
class RxAssistantDbJudge(Evaluator[Any, Any]):
    """Scores an rx-assistant reply on three axes — factual grounding against the database,
    whether the assistant's tool-use decision fit the question, and whether its overall
    trajectory (the ordered sequence of steps it took) was efficient — by delegating judgment
    to an Agent equipped with its own read access to that same database."""

    agent: Agent = field(default_factory=lambda: _judge_agent)

    async def evaluate(self, ctx: EvaluatorContext[Any, Any]) -> dict[str, EvaluationReason]:
        tool_sequence = _tool_call_sequence(ctx)
        prompt = (
            f"Patient message:\n{ctx.inputs}\n\n"
            f"Assistant's reply:\n{ctx.output}\n\n"
            f"Tools the assistant called, in order: "
            f"{' -> '.join(tool_sequence) if tool_sequence else 'none'}."
        )
        deps = await _get_judge_deps()
        result = await self.agent.run(prompt, deps=deps)
        return {
            "grounded": EvaluationReason(
                value=result.output.grounded, reason=result.output.grounding_explanation
            ),
            "tool_use": EvaluationReason(
                value=result.output.tool_use, reason=result.output.tool_use_explanation
            ),
            "trajectory": EvaluationReason(
                value=result.output.trajectory, reason=result.output.trajectory_explanation
            ),
        }


# Named instance (like chat's judges) so it can be reused consistently if this judge is ever
# also wired into the offline dataset.
rx_assistant_db_judge = RxAssistantDbJudge()
