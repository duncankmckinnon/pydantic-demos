from pydantic_evals import Case
from pydantic_evals.online import DEFAULT_CONFIG, OnlineEvaluator, SamplingContext

from applicant_support.evals.dataset import ApplicantSupportEvalInput, applicant_support_eval_dataset
from applicant_support.evals.evaluators import ReplyPresent, SHARED_EVALUATORS
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
    }

    assert {case.name for case in applicant_support_eval_dataset.cases} == expected_names
    assert all(isinstance(case, Case) for case in applicant_support_eval_dataset.cases)
    assert all(
        isinstance(case.inputs, ApplicantSupportEvalInput)
        for case in applicant_support_eval_dataset.cases
    )
    assert all(case.metadata["application_id"] == case.inputs.application_id for case in applicant_support_eval_dataset.cases)


def test_unknown_application_case_uses_unseeded_but_valid_id() -> None:
    case = next(
        case
        for case in applicant_support_eval_dataset.cases
        if case.name == "unknown_application"
    )

    assert case.inputs.application_id == "APP-9999"
    assert case.expected_output == "The application cannot be found."


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
