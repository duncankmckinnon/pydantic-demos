from types import SimpleNamespace

import pytest
from pydantic_evals import Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from applicant_support.config import AgentConfig
from applicant_support.evals.dataset import build_seed_dataset
from applicant_support.evals.evaluators import JUDGE_MODEL, SHARED_EVALUATORS
from applicant_support.evals.experiments import (
    MODEL_CANDIDATES,
    ExperimentCandidate,
    ExperimentExecutionError,
    ExperimentRequest,
    _build_case_task,
    run_comparison,
)
from applicant_support.evals.models import ApplicantSupportEvalOutput
from demo_core.settings import GatewaySettings


class FakeDataset:
    def __init__(self) -> None:
        seed = build_seed_dataset()
        self.name = seed.name
        self.cases = seed.cases[:2]
        self.evaluators = SHARED_EVALUATORS
        self.calls: list[dict] = []

    async def evaluate(self, task, **kwargs):
        outputs = [await task(case.inputs) for case in self.cases]
        self.calls.append({"kwargs": kwargs, "outputs": outputs})
        return SimpleNamespace(
            cases=[SimpleNamespace(evaluator_failures=[]) for _ in outputs],
            failures=[],
            report_evaluator_failures=[],
        )


class BrokenEvaluator(Evaluator):
    def evaluate(self, ctx: EvaluatorContext):
        raise RuntimeError("judge unavailable")


def test_model_candidates_use_gateway_verified_routes() -> None:
    choices = {candidate.model_choice for candidate in MODEL_CANDIDATES}

    assert ("openai-responses", "gpt-5.4-mini") in choices
    assert ("openai-responses", "gpt-5-mini") not in choices
    assert ("anthropic", "claude-haiku-5-5") in choices
    assert ("anthropic", "claude-haiku-5.5") not in choices


async def test_comparisons_hold_one_dimension_and_emit_complete_metadata(
    monkeypatch,
) -> None:
    import applicant_support.evals.experiments as experiments

    built_capabilities: list[tuple] = []
    run_configs = []

    def fake_build_agent(settings, *, capabilities=()):
        built_capabilities.append(tuple(capabilities))
        return object()

    async def fake_run_applicant_turn(agent, **kwargs):
        run_configs.append(kwargs["config"])
        return SimpleNamespace(output="Test reply")

    monkeypatch.setattr(experiments, "build_agent", fake_build_agent)
    monkeypatch.setattr(experiments, "get_model", lambda *args: object())
    monkeypatch.setattr(experiments, "run_applicant_turn", fake_run_applicant_turn)
    settings = GatewaySettings(api_key="pylf_v1_us_test-key")

    model_dataset = FakeDataset()
    model_request = ExperimentRequest(
        kind="model",
        comparison_group="model-group",
        candidates=[
            ExperimentCandidate(
                label="openai-mini",
                prompt="Fixed prompt",
                model=AgentConfig(
                    api_format="openai-responses", model_name="gpt-5.4-mini"
                ),
            ),
            ExperimentCandidate(
                label="anthropic-haiku",
                prompt="Fixed prompt",
                model=AgentConfig(
                    api_format="anthropic", model_name="claude-haiku-5-5"
                ),
            ),
        ],
    )
    model_summaries = await run_comparison(
        model_request,
        dataset=model_dataset,
        gateway_settings=settings,
    )

    assert {config.prompt for config in run_configs} == {"Fixed prompt"}
    assert {config.model.model_choice for config in run_configs} == {
        ("openai-responses", "gpt-5.4-mini"),
        ("anthropic", "claude-haiku-5-5"),
    }
    assert all(call["outputs"] == [
        ApplicantSupportEvalOutput(reply="Test reply"),
        ApplicantSupportEvalOutput(reply="Test reply"),
    ] for call in model_dataset.calls)
    assert all(summary.case_count == 2 for summary in model_summaries)

    run_configs.clear()
    prompt_dataset = FakeDataset()
    fixed_model = AgentConfig(api_format="openai-responses", model_name="gpt-5.4-mini")
    prompt_request = ExperimentRequest(
        kind="prompt",
        comparison_group="prompt-group",
        candidates=[
            ExperimentCandidate(label="weak", prompt="Weak prompt", model=fixed_model),
            ExperimentCandidate(
                label="tool-first", prompt="Tool-first prompt", model=fixed_model
            ),
        ],
    )
    await run_comparison(
        prompt_request,
        dataset=prompt_dataset,
        gateway_settings=settings,
    )

    assert {config.prompt for config in run_configs} == {
        "Weak prompt",
        "Tool-first prompt",
    }
    assert {config.model.model_choice for config in run_configs} == {
        ("openai-responses", "gpt-5.4-mini")
    }
    assert built_capabilities == [(), (), (), ()]
    assert model_dataset.evaluators is SHARED_EVALUATORS
    assert prompt_dataset.evaluators is SHARED_EVALUATORS
    for dataset, group, kind in (
        (model_dataset, "model-group", "model"),
        (prompt_dataset, "prompt-group", "prompt"),
    ):
        assert len(dataset.calls) == 2
        for call in dataset.calls:
            metadata = call["kwargs"]["metadata"]
            assert call["kwargs"]["name"].startswith(
                f"applicant_support_{kind}_{group}_"
            )
            assert call["kwargs"]["task_name"].startswith("applicant_support_agent:")
            assert metadata["comparison_group"] == group
            assert metadata["comparison_kind"] == kind
            assert metadata["candidate"]
            assert metadata["model"]
            assert metadata["prompt_fingerprint"]
            assert metadata["dataset"] == "applicant_support_baseline"
            assert metadata["dataset_snapshot_at"].endswith("+00:00")
            assert metadata["judge_model"] == ":".join(JUDGE_MODEL)


async def test_case_task_uses_fresh_seeded_database_every_time(monkeypatch) -> None:
    import applicant_support.evals.experiments as experiments

    starts_at = []
    paths = []

    async def fake_run_applicant_turn(agent, **kwargs):
        database = kwargs["deps"].database
        record = database.get_application("APP-1001")
        starts_at.append(record.interview_starts_at.isoformat())
        paths.append(database.path)
        database.reschedule_interview(
            "APP-1001",
            record.interview_starts_at.replace(day=27, hour=14, minute=30),
        )
        return SimpleNamespace(output="Rescheduled")

    monkeypatch.setattr(experiments, "run_applicant_turn", fake_run_applicant_turn)
    candidate = ExperimentCandidate(
        label="isolation",
        prompt="Prompt",
        model=AgentConfig(api_format="openai-responses", model_name="gpt-5.4-mini"),
    )
    task = _build_case_task(agent=object(), candidate=candidate, model=object())
    inputs = build_seed_dataset().cases[3].inputs

    await task(inputs)
    await task(inputs)

    assert starts_at == [
        "2026-10-23T10:00:00-07:00",
        "2026-10-23T10:00:00-07:00",
    ]
    assert paths[0] != paths[1]
    assert all(path.name != "evals.sqlite3" for path in paths)


async def test_comparison_rejects_task_and_evaluator_failures(monkeypatch) -> None:
    import applicant_support.evals.experiments as experiments

    monkeypatch.setattr(
        experiments, "build_agent", lambda settings, *, capabilities=(): object()
    )
    monkeypatch.setattr(experiments, "get_model", lambda *args: object())
    request = ExperimentRequest(
        kind="model",
        comparison_group="failure-group",
        candidates=[
            ExperimentCandidate(
                label="candidate-a",
                prompt="Prompt A",
                model=AgentConfig(
                    api_format="openai-responses", model_name="gpt-5.4-mini"
                ),
            ),
            ExperimentCandidate(
                label="candidate-b",
                prompt="Prompt A",
                model=AgentConfig(
                    api_format="anthropic", model_name="claude-haiku-5-5"
                ),
            ),
        ],
    )
    case = build_seed_dataset().cases[0]
    settings = GatewaySettings(api_key="pylf_v1_us_test-key")

    async def broken_task(*args, **kwargs):
        raise RuntimeError("task unavailable")

    monkeypatch.setattr(experiments, "run_applicant_turn", broken_task)
    task_failure_dataset = Dataset(
        name="task-failure",
        cases=[case],
        evaluators=[],
    )
    with pytest.raises(ExperimentExecutionError, match="task failure"):
        await run_comparison(
            request,
            dataset=task_failure_dataset,
            gateway_settings=settings,
        )

    async def successful_task(*args, **kwargs):
        return SimpleNamespace(output="A grounded response")

    monkeypatch.setattr(experiments, "run_applicant_turn", successful_task)
    evaluator_failure_dataset = Dataset(
        name="evaluator-failure",
        cases=[case],
        evaluators=[BrokenEvaluator()],
    )
    with pytest.raises(ExperimentExecutionError, match="evaluator failure"):
        await run_comparison(
            request,
            dataset=evaluator_failure_dataset,
            gateway_settings=settings,
        )


async def test_comparison_rejects_empty_dataset() -> None:
    request = ExperimentRequest(
        kind="prompt",
        candidates=[
            ExperimentCandidate(
                label="candidate-a",
                prompt="Prompt A",
                model=AgentConfig(
                    api_format="openai-responses", model_name="gpt-5.4-mini"
                ),
            ),
            ExperimentCandidate(
                label="candidate-b",
                prompt="Prompt B",
                model=AgentConfig(
                    api_format="openai-responses", model_name="gpt-5.4-mini"
                ),
            ),
        ],
    )

    with pytest.raises(ExperimentExecutionError, match="no cases"):
        await run_comparison(
            request,
            dataset=Dataset(name="empty", cases=[], evaluators=[]),
            gateway_settings=GatewaySettings(api_key="pylf_v1_us_test-key"),
        )
