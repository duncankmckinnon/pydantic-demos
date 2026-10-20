import pytest

from applicant_support.evals.experiments import ExperimentRunSummary
from applicant_support.jobs import ExperimentJobRegistry


def _summary() -> ExperimentRunSummary:
    return ExperimentRunSummary(
        kind="model",
        comparison_group="group-1",
        candidate="control",
        experiment_name="experiment-1",
        task_name="applicant_support_agent:model:control",
        case_count=3,
        model_name="openai-responses:gpt-5.4-mini",
        prompt_fingerprint="abc123def456",
    )


def test_registry_enforces_legal_transitions_and_unknown_jobs() -> None:
    registry = ExperimentJobRegistry()
    job = registry.create(kind="model", scope="smoke")

    assert job.status == "queued"
    assert registry.start(job.id).status == "running"
    completed = registry.complete(job.id, [_summary()])
    assert completed.status == "completed"
    assert completed.candidate_count == 1
    assert completed.case_count == 3
    assert completed.started_at is not None
    assert completed.completed_at is not None
    with pytest.raises(ValueError):
        registry.start(job.id)
    with pytest.raises(KeyError):
        registry.get("missing-job")


async def test_registry_captures_failures_without_secrets_or_prompts(caplog) -> None:
    registry = ExperimentJobRegistry()

    async def fail():
        raise RuntimeError(
            "pylf_v1_us_super-secret complete managed prompt body should not leak"
        )

    job = registry.launch(kind="prompt", scope="smoke", runner=fail)
    await registry.wait(job.id)
    failed = registry.get(job.id)

    assert failed.status == "failed"
    assert failed.error == "RuntimeError: experiment failed; check local logs"
    assert "pylf" not in failed.error
    assert "managed prompt" not in failed.error
    assert job.id in caplog.text
    assert "RuntimeError" in caplog.text
    assert "pylf_v1_us_super-secret" not in caplog.text
    assert "complete managed prompt body" not in caplog.text


async def test_reset_cancels_and_removes_process_local_jobs() -> None:
    registry = ExperimentJobRegistry()

    async def never_finishes():
        await __import__("asyncio").Event().wait()

    job = registry.launch(kind="model", scope="full", runner=never_finishes)
    registry.reset()

    with pytest.raises(KeyError):
        registry.get(job.id)
