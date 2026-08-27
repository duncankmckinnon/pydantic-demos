import numpy as np
from pydantic_ai.models.test import TestModel

from demo_core.settings import GatewaySettings
from rx_assistant.agent import Deps, MODEL_CHOICES, build_agent


class FakePool:
    def __init__(self, condition_rows, medication_rows):
        self._condition_rows = condition_rows
        self._medication_rows = medication_rows

    async def fetch(self, query, *args):
        if "FROM conditions" in query:
            return self._condition_rows
        return self._medication_rows


class FakeEmbeddingModel:
    def encode(self, texts, **kwargs):
        return np.zeros((len(texts), 3))


def test_model_choices_is_non_empty_list_of_pairs() -> None:
    assert len(MODEL_CHOICES) >= 1
    for api_format, model_name in MODEL_CHOICES:
        assert isinstance(api_format, str) and api_format
        assert isinstance(model_name, str) and model_name


def test_build_agent_runs_tools_with_test_model() -> None:
    settings = GatewaySettings(api_key="pylf_v1_us_test-key")
    agent = build_agent(settings)
    deps = Deps(
        pool=FakePool(
            condition_rows=[{"name": "ADHD", "distance": 0.1}],
            medication_rows=[
                {
                    "med_name": "Atrest 25mg",
                    "generic_name": "Tetrabenazine",
                    "drug_content": "...",
                    "drug_manufacturer": "Centaur",
                    "price": "335.68",
                    "prescription_required": "Rx required",
                    "condition_name": "ADHD",
                    "distance": 0.05,
                }
            ],
        ),
        embedding_model=FakeEmbeddingModel(),
    )

    with agent.override(model=TestModel()):
        result = agent.run_sync("What treats ADHD?", deps=deps)

    assert isinstance(result.output, str)
    assert result.output
