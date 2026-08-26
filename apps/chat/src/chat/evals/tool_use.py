import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

from chat.agent import MODEL_CHOICES
from demo_core.models import get_model
from demo_core.settings import GatewaySettings

ToolUseCategory = Literal["appropriate", "unnecessary", "missed_opportunity"]


class ToolUseJudgment(BaseModel):
    category: ToolUseCategory
    explanation: str


# Reuses chat.agent's own default model choice for the same reason chat.evals.dataset's and
# chat.evals.efficiency's judges do — so the three can't silently drift out of sync.
_tool_use_judge_agent = Agent(
    get_model(*MODEL_CHOICES[0], GatewaySettings()),
    name="chat_tool_use_judge",
    output_type=ToolUseJudgment,
    instructions=(
        "You judge whether a chatbot's tool usage was appropriate for the user's message. "
        "Available tools: web_search (current events or facts outside the model's own "
        "knowledge), the memory tools write_memory/read_memory/search_memory/delete_memory "
        "(remembering or recalling user-specific facts across conversations), and "
        "read_pyai_docs (Pydantic AI documentation lookups). Categorize as 'appropriate' if "
        "the tools used — or the choice to use none — fit the message; 'unnecessary' if a "
        "tool was called but wasn't needed; or 'missed_opportunity' if a tool should have "
        "been used but wasn't. Always give a short explanation for your category."
    ),
)


def _called_tool_names(ctx: EvaluatorContext[Any, Any]) -> list[str]:
    """Return the distinct tool names actually invoked during the run.

    Client-executed tools (memory, docs) each get their own `execute_tool <name>` span.
    Native/server-executed tools (web search) don't — they only show up as `tool_call` parts
    marked `builtin: true` inside a model-call span's `gen_ai.output.messages` attribute
    (see the chat.evals.efficiency-adjacent investigation that found this). Only `builtin`
    parts are read from that attribute so a client-executed tool's own request — which also
    appears there — isn't double-counted against its `execute_tool` span.
    """
    names: set[str] = set()

    for span in ctx.span_tree.find(lambda node: node.name.startswith("execute_tool ")):
        names.add(span.name.removeprefix("execute_tool "))

    for span in ctx.span_tree.find(lambda node: "gen_ai.output.messages" in node.attributes):
        raw = span.attributes.get("gen_ai.output.messages")
        try:
            messages = json.loads(raw) if isinstance(raw, str) else []
        except ValueError:
            continue
        for message in messages:
            for part in message.get("parts", []):
                if part.get("type") == "tool_call" and part.get("builtin"):
                    names.add(part.get("name", "unknown"))

    return sorted(names)


@dataclass
class ToolUseAppropriateness(Evaluator[Any, Any]):
    """Scores whether tool usage (or the lack of it) fit the user's message, by delegating
    judgment to an Agent given the input, output, and which tools the run actually called."""

    agent: Agent = field(default_factory=lambda: _tool_use_judge_agent)

    async def evaluate(self, ctx: EvaluatorContext[Any, Any]) -> EvaluationReason:
        tools_called = _called_tool_names(ctx)
        prompt = (
            f"User message:\n{ctx.inputs}\n\n"
            f"Reply:\n{ctx.output}\n\n"
            f"Tools called: {', '.join(tools_called) if tools_called else 'none'}."
        )
        result = await self.agent.run(prompt)
        return EvaluationReason(value=result.output.category, reason=result.output.explanation)


# Named instance (like chat.evals.dataset.chat_quality_judge and
# chat.evals.efficiency.chat_efficiency_judge) so the offline dataset and the online capability
# share one evaluator instead of two copies that can drift apart.
chat_tool_use_judge = ToolUseAppropriateness()
