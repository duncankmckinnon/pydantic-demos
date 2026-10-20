from types import SimpleNamespace

from pydantic_evals import Case, Dataset

from applicant_support.evals.dataset import (
    DATASET_NAME,
    ApplicantSupportCaseMetadata,
    ApplicantSupportEvalInput,
    build_seed_dataset,
)
from applicant_support.evals.evaluators import SHARED_EVALUATORS
from applicant_support.evals.hosted import load_hosted_dataset, sync_seed_dataset
from applicant_support.evals.models import ApplicantSupportEvalOutput


class FakeDatasetClient:
    def __init__(self, hosted_dataset: Dataset | None = None) -> None:
        self.hosted_dataset = hosted_dataset
        self.push_calls: list[tuple[Dataset, dict]] = []
        self.get_calls: list[tuple[str, dict]] = []

    def push_dataset(self, dataset: Dataset, **kwargs):
        self.push_calls.append((dataset, kwargs))
        return SimpleNamespace(id="dataset-123", name=DATASET_NAME)

    def get_dataset(self, id_or_name: str, **kwargs):
        self.get_calls.append((id_or_name, kwargs))
        return self.hosted_dataset


def _hosted_dataset_with_trace_case() -> Dataset:
    seed = build_seed_dataset()
    trace_case = Case(
        name="trace-derived-frustration",
        inputs=ApplicantSupportEvalInput(
            application_id="APP-1002",
            question="I have been waiting forever. What is happening?",
            scenario_category="application_status",
        ),
        expected_output=ApplicantSupportEvalOutput(
            reply="Acknowledge the delay, look up the application, and explain the status."
        ),
        metadata=ApplicantSupportCaseMetadata(
            application_id="APP-1002",
            category="application_status",
            complexity="moderate",
            provenance="trace",
            tags=["frustration", "trace"],
            required_tool="get_application_status",
        ),
    )
    return Dataset(
        name=DATASET_NAME,
        cases=[*seed.cases, trace_case],
        evaluators=[],
    )


def test_sync_updates_named_seed_cases_without_deleting_others() -> None:
    client = FakeDatasetClient()

    detail = sync_seed_dataset(client)

    assert detail.id == "dataset-123"
    assert len(client.push_calls) == 1
    dataset, kwargs = client.push_calls[0]
    assert dataset.name == DATASET_NAME
    assert kwargs == {"name": DATASET_NAME, "on_case_conflict": "update"}
    assert {case.name for case in dataset.cases} >= {"lookup_tool_regression"}


def test_load_fetches_typed_hosted_cases_and_applies_code_evaluators() -> None:
    client = FakeDatasetClient(_hosted_dataset_with_trace_case())

    dataset = load_hosted_dataset(client)

    assert client.get_calls == [
        (
            DATASET_NAME,
            {
                "input_type": ApplicantSupportEvalInput,
                "output_type": ApplicantSupportEvalOutput,
                "metadata_type": ApplicantSupportCaseMetadata,
            },
        )
    ]
    assert "trace-derived-frustration" in {case.name for case in dataset.cases}
    assert list(dataset.evaluators) == SHARED_EVALUATORS
    assert client.push_calls == []
