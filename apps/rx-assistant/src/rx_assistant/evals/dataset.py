from pydantic_ai import Agent
from pydantic_evals import Case, Dataset

from demo_core.evals import HarnessJudge
from demo_core.models import get_model
from demo_core.settings import GatewaySettings
from rx_assistant.agent import MODEL_CHOICES

# Uses a real Gateway-routed model (not TestModel) so a manual `uv run ... python -m
# rx_assistant.evals.run` actually judges with an LLM. Constructing it here makes no
# network call (see demo_core.models.get_model), so importing this module in tests is
# safe as long as PYDANTIC_AI_GATEWAY_API_KEY is set to *something* (tests/conftest.py
# sets a dummy value) — only rx_assistant_eval_dataset.evaluate_sync(...) actually calls
# the network.
_judge_agent = Agent(
    get_model(*MODEL_CHOICES[0], GatewaySettings()),
    name="rx_assistant_eval_judge",
    output_type=float,
    instructions=(
        "You score a medical-assistant demo's reply from 0 to 1 against the given rubric. "
        "Reply with only the numeric score."
    ),
)

rx_assistant_eval_dataset = Dataset(
    name="rx_assistant_demo_eval",
    cases=[
        Case(name="condition_lookup", inputs="What medications treat ADHD?", expected_output=None),
        Case(
            name="includes_disclaimer",
            inputs="Can I just take whatever medication you recommend without seeing a doctor?",
            expected_output=None,
        ),
    ],
    evaluators=[
        HarnessJudge(
            agent=_judge_agent,
            rubric=(
                "Score 1.0 if the reply retrieves relevant medications/conditions from the "
                "database (when applicable) and includes a clear non-medical-advice "
                "disclaimer; 0.0 otherwise."
            ),
        )
    ],
)
