from pydantic import BaseModel
from pydantic_evals import Case, Dataset


class ApplicantSupportEvalInput(BaseModel):
    application_id: str
    question: str


def _case(
    name: str,
    application_id: str,
    question: str,
    expected_output: str,
    category: str,
) -> Case[ApplicantSupportEvalInput, str, dict[str, str]]:
    return Case(
        name=name,
        inputs=ApplicantSupportEvalInput(
            application_id=application_id,
            question=question,
        ),
        metadata={"application_id": application_id, "category": category},
        expected_output=expected_output,
    )


applicant_support_eval_dataset = Dataset(
    name="applicant_support_baseline",
    cases=[
        _case(
            "screening_passed",
            "APP-1001",
            "Did I pass the screening?",
            "The applicant passed screening and has a recruiter call scheduled.",
            "application_status",
        ),
        _case(
            "screening_pending",
            "APP-1002",
            "What is the status of my application?",
            "The application is still under review.",
            "application_status",
        ),
        _case(
            "screening_not_selected",
            "APP-1003",
            "Did I make it through screening?",
            "The applicant was not selected after screening.",
            "application_status",
        ),
        _case(
            "reschedule_call",
            "APP-1001",
            "Can you move my recruiter call to October 27, 2026 at 2:30 PM Pacific?",
            "The existing recruiter call is rescheduled to the requested time.",
            "rescheduling",
        ),
        _case(
            "open_role",
            "APP-1001",
            "Is the Customer Support Specialist role open?",
            "The Customer Support Specialist role is open in Austin, Texas.",
            "role_search",
        ),
        _case(
            "closed_role",
            "APP-1002",
            "Is the Product Designer role open?",
            "The Product Designer role is closed.",
            "role_search",
        ),
        _case(
            "unknown_application",
            "APP-9999",
            "What happened with my application?",
            "The application cannot be found.",
            "application_status",
        ),
        _case(
            "does_not_invent_salary",
            "APP-1001",
            "What salary is listed for my role?",
            "The available applicant data does not include salary information.",
            "groundedness",
        ),
    ],
)
