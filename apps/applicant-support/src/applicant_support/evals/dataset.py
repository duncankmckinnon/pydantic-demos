from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic_evals import Case, Dataset

from applicant_support.evals.evaluators import SHARED_EVALUATORS
from applicant_support.evals.models import ApplicantSupportEvalOutput

DATASET_NAME = "applicant_support_baseline"


class ApplicantSupportEvalInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    application_id: str
    question: str
    scenario_category: str


class ApplicantSupportCaseMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    application_id: str
    category: str
    complexity: Literal["simple", "moderate", "complex"]
    provenance: Literal["seed", "trace"]
    tags: list[str]
    required_tool: str | None = None


def _case(
    name: str,
    application_id: str,
    question: str,
    expected_reply: str,
    category: str,
    *,
    complexity: Literal["simple", "moderate", "complex"] = "simple",
    required_tool: str | None = None,
    tags: tuple[str, ...] = (),
) -> Case[
    ApplicantSupportEvalInput,
    ApplicantSupportEvalOutput,
    ApplicantSupportCaseMetadata,
]:
    return Case(
        name=name,
        inputs=ApplicantSupportEvalInput(
            application_id=application_id,
            question=question,
            scenario_category=category,
        ),
        metadata=ApplicantSupportCaseMetadata(
            application_id=application_id,
            category=category,
            complexity=complexity,
            provenance="seed",
            tags=list(tags or (category,)),
            required_tool=required_tool,
        ),
        expected_output=ApplicantSupportEvalOutput(reply=expected_reply),
    )


def build_seed_dataset() -> Dataset[
    ApplicantSupportEvalInput,
    ApplicantSupportEvalOutput,
    ApplicantSupportCaseMetadata,
]:
    return Dataset[
        ApplicantSupportEvalInput,
        ApplicantSupportEvalOutput,
        ApplicantSupportCaseMetadata,
    ](
        name=DATASET_NAME,
        cases=[
            _case(
                "screening_passed",
                "APP-1001",
                "Did I pass the screening?",
                "The applicant passed screening and has a recruiter call scheduled.",
                "application_status",
                required_tool="get_application_status",
                tags=("status", "positive"),
            ),
            _case(
                "screening_pending",
                "APP-1002",
                "What is the status of my application?",
                "The application is still under review.",
                "application_status",
                required_tool="get_application_status",
                tags=("status", "pending"),
            ),
            _case(
                "screening_not_selected",
                "APP-1003",
                "Did I make it through screening?",
                "The applicant was not selected after screening.",
                "application_status",
                required_tool="get_application_status",
                tags=("status", "negative"),
            ),
            _case(
                "reschedule_call",
                "APP-1001",
                "Can you move my recruiter call to October 27, 2026 at 2:30 PM Pacific?",
                "The existing recruiter call is rescheduled to the requested time.",
                "rescheduling",
                complexity="complex",
                required_tool="reschedule_screening_call",
                tags=("mutation", "scheduling"),
            ),
            _case(
                "open_role",
                "APP-1001",
                "Is the Customer Support Specialist role open?",
                "The Customer Support Specialist role is open in Austin, Texas.",
                "role_search",
                required_tool="search_roles",
                tags=("roles", "open"),
            ),
            _case(
                "closed_role",
                "APP-1002",
                "Is the Product Designer role open?",
                "The Product Designer role is closed.",
                "role_search",
                required_tool="search_roles",
                tags=("roles", "closed"),
            ),
            _case(
                "unknown_application",
                "APP-9999",
                "What happened with my application?",
                "The application cannot be found.",
                "application_status",
                required_tool="get_application_status",
                tags=("status", "not-found"),
            ),
            _case(
                "does_not_invent_salary",
                "APP-1001",
                "What salary is listed for my role?",
                "The available applicant data does not include salary information.",
                "groundedness",
                complexity="moderate",
                required_tool="get_application_status",
                tags=("groundedness", "missing-data"),
            ),
            _case(
                "lookup_tool_regression",
                "APP-1001",
                "Can you look up my application and tell me what happens next?",
                "Look up the current application status and give the applicant a helpful next step.",
                "application_status",
                complexity="moderate",
                required_tool="get_application_status",
                tags=("regression", "tool-selection", "status"),
            ),
        ],
        evaluators=SHARED_EVALUATORS,
    )


# Kept for imports used by the existing manual runner. Experiments will load hosted cases.
applicant_support_eval_dataset = build_seed_dataset()
