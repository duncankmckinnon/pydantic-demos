from __future__ import annotations

import os
from typing import Any, cast

from logfire.experimental.api_client import DatasetDetail, LogfireAPIClient
from pydantic_evals import Dataset

from applicant_support.evals.dataset import (
    DATASET_NAME,
    ApplicantSupportCaseMetadata,
    ApplicantSupportEvalInput,
    build_seed_dataset,
)
from applicant_support.evals.evaluators import SHARED_EVALUATORS
from applicant_support.evals.models import ApplicantSupportEvalOutput

HostedDataset = Dataset[
    ApplicantSupportEvalInput,
    ApplicantSupportEvalOutput,
    ApplicantSupportCaseMetadata,
]


def create_dataset_client() -> LogfireAPIClient:
    api_key = os.environ.get("LOGFIRE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "LOGFIRE_API_KEY is required for hosted applicant-support datasets."
        )
    return LogfireAPIClient(
        api_key=api_key,
        base_url=os.environ.get(
            "LOGFIRE_BASE_URL", "https://logfire-us.pydantic.dev"
        ),
    )


def sync_seed_dataset(client: Any) -> DatasetDetail:
    seed = build_seed_dataset()
    hosted_seed = Dataset[
        ApplicantSupportEvalInput,
        ApplicantSupportEvalOutput,
        ApplicantSupportCaseMetadata,
    ](
        name=seed.name,
        cases=list(seed.cases),
        # Evaluators are code, not dataset content. Keeping them out of the
        # hosted payload makes the dataset editable in Logfire and avoids
        # trying to serialize model clients and their internal locks.
        evaluators=[],
    )
    return cast(
        DatasetDetail,
        client.push_dataset(
            hosted_seed,
            name=DATASET_NAME,
            on_case_conflict="update",
        ),
    )


def load_hosted_dataset(client: Any) -> HostedDataset:
    dataset = client.get_dataset(
        DATASET_NAME,
        input_type=ApplicantSupportEvalInput,
        output_type=ApplicantSupportEvalOutput,
        metadata_type=ApplicantSupportCaseMetadata,
    )
    if not isinstance(dataset, Dataset):
        raise RuntimeError(
            f"Hosted dataset {DATASET_NAME!r} did not include its cases."
        )
    dataset.evaluators = SHARED_EVALUATORS
    return cast(HostedDataset, dataset)
