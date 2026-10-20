from pydantic_evals import Case

from applicant_support.evals.dataset import ApplicantSupportEvalInput, applicant_support_eval_dataset


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
