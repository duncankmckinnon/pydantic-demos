from __future__ import annotations

import hashlib
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.models import Model
from pydantic_evals import Dataset

from applicant_support.agent import AgentDeps, build_agent
from applicant_support.config import (
    ALLOWED_MODELS,
    AgentConfig,
    ResolvedAgentConfig,
    resolve_agent_config,
)
from applicant_support.db import ApplicantDatabase
from applicant_support.evals.dataset import (
    ApplicantSupportCaseMetadata,
    ApplicantSupportEvalInput,
)
from applicant_support.evals.evaluators import JUDGE_MODEL
from applicant_support.evals.models import ApplicantSupportEvalOutput
from applicant_support.service import run_applicant_turn
from demo_core.models import get_model
from demo_core.settings import GatewaySettings

ExperimentKind = Literal["model", "prompt"]

MODEL_CANDIDATES = (
    AgentConfig(api_format="openai-responses", model_name="gpt-5-mini"),
    AgentConfig(api_format="anthropic", model_name="claude-haiku-5.5"),
)

PROMPT_CANDIDATES = (
    (
        "generic-hr",
        "You are an HR representative. Answer the applicant's questions and use the "
        "available tools when helpful.",
    ),
    (
        "tool-first",
        "You are an HR applicant-support representative. Before answering any question "
        "about an application, screening result, interview, role, or requested schedule "
        "change, use the matching tool and ground the reply in its result. Be concise, "
        "professional, and acknowledge frustration directly.",
    ),
)


class ExperimentCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    prompt: str = Field(min_length=1)
    model: AgentConfig

    @property
    def prompt_fingerprint(self) -> str:
        return hashlib.sha256(self.prompt.encode()).hexdigest()[:12]

    def resolved_config(self) -> ResolvedAgentConfig:
        if self.model.model_choice not in ALLOWED_MODELS:
            raise ValueError(f"Unsupported experiment model: {self.model.model_choice!r}")
        return ResolvedAgentConfig(
            prompt=self.prompt,
            model=self.model,
            prompt_fingerprint=self.prompt_fingerprint,
            prompt_source="experiment",
            model_source="experiment",
        )


class ExperimentRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: ExperimentKind
    comparison_group: str = Field(default_factory=lambda: uuid4().hex[:12])
    candidates: list[ExperimentCandidate] = Field(min_length=2)


class ExperimentRunSummary(BaseModel):
    kind: ExperimentKind
    comparison_group: str
    candidate: str
    experiment_name: str
    task_name: str
    case_count: int
    model_name: str
    prompt_fingerprint: str


def build_experiment_request(
    kind: ExperimentKind,
    *,
    control: ResolvedAgentConfig | None = None,
    comparison_group: str | None = None,
) -> ExperimentRequest:
    control = control or resolve_agent_config()
    candidates = [
        ExperimentCandidate(
            label="managed-control",
            prompt=control.prompt,
            model=control.model,
        )
    ]
    if kind == "model":
        for model in MODEL_CANDIDATES:
            if model.model_choice != control.model.model_choice:
                candidates.append(
                    ExperimentCandidate(
                        label=re.sub(r"[^a-z0-9]+", "-", model.model_name.lower()).strip("-"),
                        prompt=control.prompt,
                        model=model,
                    )
                )
    else:
        for label, prompt in PROMPT_CANDIDATES:
            if prompt != control.prompt:
                candidates.append(
                    ExperimentCandidate(label=label, prompt=prompt, model=control.model)
                )
    return ExperimentRequest(
        kind=kind,
        comparison_group=comparison_group or uuid4().hex[:12],
        candidates=candidates,
    )


def _build_case_task(
    *,
    agent: Any,
    candidate: ExperimentCandidate,
    model: Model,
) -> Callable[[ApplicantSupportEvalInput], Any]:
    config = candidate.resolved_config()

    async def run_case(inputs: ApplicantSupportEvalInput) -> ApplicantSupportEvalOutput:
        with tempfile.TemporaryDirectory(prefix="applicant-support-eval-") as temp_dir:
            database = ApplicantDatabase(Path(temp_dir) / "case.sqlite3")
            database.initialize()
            result = await run_applicant_turn(
                agent,
                question=inputs.question,
                deps=AgentDeps(
                    database=database,
                    application_id=inputs.application_id,
                ),
                config=config,
                model=model,
                metadata={
                    "experiment_candidate": candidate.label,
                    "force_online_evals": False,
                },
            )
            return ApplicantSupportEvalOutput(reply=str(result.output))

    return run_case


async def run_comparison(
    request: ExperimentRequest,
    *,
    dataset: Dataset[
        ApplicantSupportEvalInput,
        ApplicantSupportEvalOutput,
        ApplicantSupportCaseMetadata,
    ],
    gateway_settings: GatewaySettings,
) -> list[ExperimentRunSummary]:
    summaries: list[ExperimentRunSummary] = []
    for candidate in request.candidates:
        config = candidate.resolved_config()
        agent = build_agent(gateway_settings, capabilities=())
        model = get_model(*candidate.model.model_choice, gateway_settings)
        experiment_name = (
            f"applicant_support_{request.kind}_{request.comparison_group}_"
            f"{candidate.label}"
        )
        task_name = f"applicant_support_agent:{request.kind}:{candidate.label}"
        metadata = {
            "comparison_kind": request.kind,
            "comparison_group": request.comparison_group,
            "candidate": candidate.label,
            "model": ":".join(candidate.model.model_choice),
            "prompt_fingerprint": candidate.prompt_fingerprint,
            "dataset": dataset.name,
            "judge_model": ":".join(JUDGE_MODEL),
        }
        await dataset.evaluate(
            _build_case_task(agent=agent, candidate=candidate, model=model),
            name=experiment_name,
            task_name=task_name,
            metadata=metadata,
            max_concurrency=1,
            progress=False,
        )
        summaries.append(
            ExperimentRunSummary(
                kind=request.kind,
                comparison_group=request.comparison_group,
                candidate=candidate.label,
                experiment_name=experiment_name,
                task_name=task_name,
                case_count=len(dataset.cases),
                model_name=metadata["model"],
                prompt_fingerprint=config.prompt_fingerprint,
            )
        )
    return summaries


def _smoke_dataset(
    dataset: Dataset[
        ApplicantSupportEvalInput,
        ApplicantSupportEvalOutput,
        ApplicantSupportCaseMetadata,
    ],
) -> Dataset[
    ApplicantSupportEvalInput,
    ApplicantSupportEvalOutput,
    ApplicantSupportCaseMetadata,
]:
    return Dataset(
        name=dataset.name,
        cases=dataset.cases[:3],
        evaluators=dataset.evaluators,
        report_evaluators=dataset.report_evaluators,
    )


async def run_smoke_comparison(
    kind: ExperimentKind,
    *,
    dataset: Dataset,
    gateway_settings: GatewaySettings,
    control: ResolvedAgentConfig | None = None,
) -> list[ExperimentRunSummary]:
    request = build_experiment_request(kind, control=control)
    return await run_comparison(
        request,
        dataset=_smoke_dataset(dataset),
        gateway_settings=gateway_settings,
    )


async def run_full_comparison(
    kind: ExperimentKind,
    *,
    dataset: Dataset,
    gateway_settings: GatewaySettings,
    control: ResolvedAgentConfig | None = None,
) -> list[ExperimentRunSummary]:
    request = build_experiment_request(kind, control=control)
    return await run_comparison(
        request,
        dataset=dataset,
        gateway_settings=gateway_settings,
    )
