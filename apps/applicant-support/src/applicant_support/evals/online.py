from dataclasses import replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_evals.online import DEFAULT_CONFIG, OnlineEvaluator, SamplingContext
from pydantic_evals.online_capability import OnlineEvaluation

from applicant_support.evals.evaluators import (
    SHARED_EVALUATORS,
    frustration_handling,
    grounded_correctness,
    professionalism,
    reply_present,
    tool_use_appropriateness,
)


def _online_sample_rate(ctx: SamplingContext) -> float:
    if ctx.metadata and ctx.metadata.get("force_online_evals") is True:
        return 1.0
    return 0.2


_ONLINE_ENTRIES = [
    reply_present,
    OnlineEvaluator(
        evaluator=tool_use_appropriateness,
        sample_rate=_online_sample_rate,
    ),
    OnlineEvaluator(
        evaluator=grounded_correctness,
        sample_rate=_online_sample_rate,
    ),
    OnlineEvaluator(
        evaluator=professionalism,
        sample_rate=_online_sample_rate,
    ),
    OnlineEvaluator(
        evaluator=frustration_handling,
        sample_rate=_online_sample_rate,
    ),
]

# Canonical definition used by identity tests and documentation. The live agent attaches the
# factory below so Pydantic Evals' pre-run sampler can see each run's demo override metadata.
APPLICANT_SUPPORT_ONLINE_EVALUATION = OnlineEvaluation(evaluators=_ONLINE_ENTRIES)


def applicant_support_online_evaluation(
    ctx: RunContext[Any],
) -> OnlineEvaluation:
    metadata = {**(DEFAULT_CONFIG.metadata or {}), **(ctx.metadata or {})}
    config = replace(DEFAULT_CONFIG, metadata=metadata)
    return OnlineEvaluation(evaluators=_ONLINE_ENTRIES, config=config)


assert [
    entry.evaluator if isinstance(entry, OnlineEvaluator) else entry
    for entry in _ONLINE_ENTRIES
] == SHARED_EVALUATORS
