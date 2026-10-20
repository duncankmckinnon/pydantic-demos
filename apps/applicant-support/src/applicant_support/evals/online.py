from pydantic_evals.online import OnlineEvaluator
from pydantic_evals.online_capability import OnlineEvaluation

from applicant_support.evals.evaluators import (
    SHARED_EVALUATORS,
    applicant_support_quality,
)

_ONLINE_ENTRIES = [
    OnlineEvaluator(
        evaluator=applicant_support_quality,
        sample_rate=1.0,
    ),
]

APPLICANT_SUPPORT_ONLINE_EVALUATION = OnlineEvaluation(evaluators=_ONLINE_ENTRIES)


assert [
    entry.evaluator if isinstance(entry, OnlineEvaluator) else entry
    for entry in _ONLINE_ENTRIES
] == SHARED_EVALUATORS
