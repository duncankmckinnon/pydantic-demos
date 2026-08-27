from types import SimpleNamespace

from pydantic_ai.models.test import TestModel

from demo_core.settings import GatewaySettings
from rx_assistant.agent import Deps, MODEL_CHOICES, build_agent


class FakePool:
    def __init__(self, medication_rows):
        self._medication_rows = medication_rows

    async def fetch(self, query, *args):
        return self._medication_rows


class FakeEmbeddingModel:
    async def embed_query(self, text):
        return SimpleNamespace(embeddings=[[0.0, 0.0, 0.0]])

    async def embed_documents(self, texts):
        return SimpleNamespace(embeddings=[[0.0, 0.0, 0.0] for _ in texts])


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
            medication_rows=[
                {
                    "med_name": "Vyvanse",
                    "med_url": "https://www.drugs.com/vyvanse.html",
                    "generic_name": "lisdexamfetamine",
                    "brand_names": "Vyvanse",
                    "drug_classes": "CNS stimulants",
                    "side_effects": "insomnia, dry mouth",
                    "rx_otc": "Rx",
                    "pregnancy_category": "C",
                    "csa": "2",
                    "alcohol": "X",
                    "condition_name": "ADHD",
                    "distance": 0.05,
                }
            ],
        ),
        embedding_model=FakeEmbeddingModel(),
    )

    # Excludes the SubAgents-provided delegate_task tool: TestModel's default call_tools="all"
    # would otherwise call it too, actually invoking the web-research sub-agent's real
    # Gateway-routed model.
    with agent.override(model=TestModel(call_tools=["search_medications"])):
        result = agent.run_sync("What treats ADHD?", deps=deps)

    assert isinstance(result.output, str)
    assert result.output
