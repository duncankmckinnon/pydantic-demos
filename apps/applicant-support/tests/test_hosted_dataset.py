import json
from types import SimpleNamespace

import httpx
from logfire.experimental.api_client import (
    LogfireAPIClient,
    _get_dataset_type_args,
    _serialize_evaluators,
)
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
    assert list(dataset.evaluators) == []
    assert all(not case.evaluators for case in dataset.cases)
    assert _serialize_evaluators(dataset.evaluators) == []
    assert _get_dataset_type_args(dataset) == (
        ApplicantSupportEvalInput,
        ApplicantSupportEvalOutput,
        ApplicantSupportCaseMetadata,
    )


def test_seed_dataset_exposes_runtime_schema_types_to_logfire_sdk() -> None:
    assert _get_dataset_type_args(build_seed_dataset()) == (
        ApplicantSupportEvalInput,
        ApplicantSupportEvalOutput,
        ApplicantSupportCaseMetadata,
    )


def test_sync_serializes_schemas_and_cases_through_real_sdk_client() -> None:
    requests: list[tuple[str, str, dict | None]] = []
    dataset_id = "00000000-0000-0000-0000-000000000123"

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, body))
        if request.url.path.endswith("/import/"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"id": dataset_id, "name": DATASET_NAME})

    transport = httpx.MockTransport(handle)
    with httpx.Client(base_url="https://logfire.invalid", transport=transport) as http:
        client = LogfireAPIClient(client=http)
        detail = sync_seed_dataset(client)

    assert str(detail["id"]) == dataset_id
    create_payload = requests[0][2]
    assert create_payload is not None
    assert create_payload["evaluators"] == []
    assert create_payload["report_evaluators"] == []
    assert create_payload["input_schema"]["type"] == "object"
    assert create_payload["output_schema"]["type"] == "object"
    assert create_payload["metadata_schema"]["type"] == "object"
    import_payload = next(
        body for method, path, body in requests if path.endswith("/import/")
    )
    assert import_payload is not None
    assert len(import_payload["cases"]) == len(build_seed_dataset().cases)
    assert all(case["evaluators"] == [] for case in import_payload["cases"])


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


def test_sync_cli_prints_mapping_dataset_detail(monkeypatch, capsys) -> None:
    import applicant_support.evals.sync as sync_module

    class FakeClientContext:
        def __enter__(self):
            return object()

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(sync_module, "create_dataset_client", FakeClientContext)
    monkeypatch.setattr(
        sync_module,
        "sync_seed_dataset",
        lambda client: {
            "id": "00000000-0000-0000-0000-000000000123",
            "name": DATASET_NAME,
        },
    )

    sync_module.main()

    output = capsys.readouterr().out
    assert "Dataset ID: 00000000-0000-0000-0000-000000000123" in output
