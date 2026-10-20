from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from applicant_support.evals.experiments import (
    ExperimentKind,
    ExperimentRunSummary,
)

JobStatus = Literal["queued", "running", "completed", "failed"]
ExperimentScope = Literal["smoke", "full"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ExperimentJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: JobStatus
    kind: ExperimentKind
    scope: ExperimentScope
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    candidate_count: int = 0
    case_count: int = 0
    summaries: list[ExperimentRunSummary] = Field(default_factory=list)
    error: str | None = None


class ExperimentJobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, ExperimentJob] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def create(
        self,
        *,
        kind: ExperimentKind,
        scope: ExperimentScope,
    ) -> ExperimentJob:
        job = ExperimentJob(
            id=uuid4().hex,
            status="queued",
            kind=kind,
            scope=scope,
            created_at=_now(),
        )
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> ExperimentJob:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise KeyError(f"Unknown experiment job: {job_id}") from None

    def start(self, job_id: str) -> ExperimentJob:
        job = self.get(job_id)
        if job.status != "queued":
            raise ValueError(f"Cannot start a {job.status} experiment job")
        job.status = "running"
        job.started_at = _now()
        return job

    def complete(
        self,
        job_id: str,
        summaries: list[ExperimentRunSummary],
    ) -> ExperimentJob:
        job = self.get(job_id)
        if job.status != "running":
            raise ValueError(f"Cannot complete a {job.status} experiment job")
        job.status = "completed"
        job.completed_at = _now()
        job.summaries = summaries
        job.candidate_count = len(summaries)
        job.case_count = summaries[0].case_count if summaries else 0
        return job

    def fail(self, job_id: str, error: Exception) -> ExperimentJob:
        job = self.get(job_id)
        if job.status not in ("queued", "running"):
            raise ValueError(f"Cannot fail a {job.status} experiment job")
        job.status = "failed"
        job.completed_at = _now()
        job.error = f"{type(error).__name__}: experiment failed; check local logs"
        return job

    def launch(
        self,
        *,
        kind: ExperimentKind,
        scope: ExperimentScope,
        runner: Callable[[], Awaitable[list[ExperimentRunSummary]]],
    ) -> ExperimentJob:
        job = self.create(kind=kind, scope=scope)

        async def execute() -> None:
            try:
                self.start(job.id)
                summaries = await runner()
                self.complete(job.id, summaries)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.fail(job.id, exc)

        self._tasks[job.id] = asyncio.create_task(
            execute(), name=f"applicant-support-experiment-{job.id}"
        )
        return job

    async def wait(self, job_id: str) -> None:
        task = self._tasks.get(job_id)
        if task is None:
            self.get(job_id)
            return
        await task

    def reset(self) -> None:
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        self._tasks.clear()
        self._jobs.clear()
