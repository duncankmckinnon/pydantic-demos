from pydantic_evals import Case
from pydantic_evals.online import DEFAULT_CONFIG, OnlineEvaluator, SamplingContext

from applicant_support.evals.dataset import (
    DATASET_NAME,
    ApplicantSupportCaseMetadata,
    ApplicantSupportEvalInput,
    build_seed_dataset,
)
from applicant_support.evals.evaluators import ReplyPresent, SHARED_EVALUATORS
from applicant_support.evals.models import ApplicantSupportEvalOutput
from applicant_support.evals.online import (
    APPLICANT_SUPPORT_ONLINE_EVALUATION,
    _online_sample_rate,
)


def test_dataset_covers_initial_applicant_support_scenarios() -> None:
    expected_names = {
        "screening_passed",
        "screening_pending",
        "screening_not_selected",
        "reschedule_call",
        "open_role",
        "closed_role",
        "unknown_application",
        "does_not_invent_salary",
        "lookup_tool_regression",
        "unsupported_profile_lookup",
    }
    dataset = build_seed_dataset()

    assert dataset.name == DATASET_NAME == "applicant_support_baseline"
    assert {case.name for case in dataset.cases} == expected_names
    assert len({case.name for case in dataset.cases}) == len(dataset.cases)
    assert all(isinstance(case, Case) for case in dataset.cases)
    assert all(
        isinstance(case.inputs, ApplicantSupportEvalInput)
        for case in dataset.cases
    )
    assert all(
        isinstance(case.expected_output, ApplicantSupportEvalOutput)
        for case in dataset.cases
    )
    assert all(
        isinstance(case.metadata, ApplicantSupportCaseMetadata)
        for case in dataset.cases
    )
    assert all(case.metadata.complexity for case in dataset.cases)
    assert all(case.metadata.provenance == "seed" for case in dataset.cases)
    assert all(case.metadata.tags for case in dataset.cases)
    assert all(isinstance(case.inputs.model_dump(), dict) for case in dataset.cases)
    assert all(isinstance(case.expected_output.model_dump(), dict) for case in dataset.cases)
    assert all(isinstance(case.metadata.model_dump(), dict) for case in dataset.cases)
    assert list(dataset.evaluators) == SHARED_EVALUATORS


def test_unknown_application_case_uses_unseeded_but_valid_id() -> None:
    dataset = build_seed_dataset()
    case = next(
        case
        for case in dataset.cases
        if case.name == "unknown_application"
    )

    assert case.inputs.application_id == "APP-9999"
    assert case.expected_output.reply == "The application cannot be found."
    assert case.metadata.required_tool == "get_application_status"


def test_lookup_regression_requires_status_tool_and_helpful_reply() -> None:
    case = next(
        case
        for case in build_seed_dataset().cases
        if case.name == "lookup_tool_regression"
    )

    assert case.metadata.required_tool == "get_application_status"
    assert "cannot help" not in case.expected_output.reply.lower()
    assert "look up" in case.expected_output.reply.lower()


def test_profile_lookup_case_requires_a_bounded_no_tool_response() -> None:
    case = next(
        case
        for case in build_seed_dataset().cases
        if case.name == "unsupported_profile_lookup"
    )

    assert case.metadata.required_tool is None
    assert case.metadata.complexity == "moderate"
    assert "unsupported-tool" in case.metadata.tags
    assert "email" in case.inputs.question.lower()
    assert "cannot retrieve" in case.expected_output.reply.lower()


def test_online_evaluation_reuses_shared_evaluator_instances() -> None:
    entries = APPLICANT_SUPPORT_ONLINE_EVALUATION.evaluators
    unwrapped = [
        entry.evaluator if isinstance(entry, OnlineEvaluator) else entry
        for entry in entries
    ]

    assert unwrapped == SHARED_EVALUATORS
    assert isinstance(entries[0], ReplyPresent)
    assert all(isinstance(entry, OnlineEvaluator) for entry in entries[1:])
    assert all(entry.sample_rate is _online_sample_rate for entry in entries[1:])


def test_online_judge_sampling_can_be_forced_for_tagged_demo_traffic() -> None:
    routine = SamplingContext(
        evaluator=SHARED_EVALUATORS[1],
        inputs="status?",
        metadata={"force_online_evals": False},
        call_seed=0.5,
    )
    demo = SamplingContext(
        evaluator=SHARED_EVALUATORS[1],
        inputs="status?",
        metadata={"force_online_evals": True},
        call_seed=0.5,
    )

    assert _online_sample_rate(routine) == 0.2
    assert _online_sample_rate(demo) == 1.0


def test_test_suite_disables_online_evaluation_globally() -> None:
    assert DEFAULT_CONFIG.should_evaluate() is False
