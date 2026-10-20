from pydantic_evals.online import OnlineEvaluator
from pydantic_evals.online_capability import OnlineEvaluation

from applicant_support.evals.evaluators import (
    SHARED_EVALUATORS,
    frustration_handling,
    grounded_correctness,
    professionalism,
    reply_present,
    tool_use_appropriateness,
)

_ONLINE_ENTRIES = [
    OnlineEvaluator(
        evaluator=reply_present,
        sample_rate=1.0,
    ),
    OnlineEvaluator(
        evaluator=tool_use_appropriateness,
        sample_rate=1.0,
    ),
    OnlineEvaluator(
        evaluator=grounded_correctness,
        sample_rate=1.0,
    ),
    OnlineEvaluator(
        evaluator=professionalism,
        sample_rate=1.0,
    ),
    OnlineEvaluator(
        evaluator=frustration_handling,
        sample_rate=1.0,
    ),
]

APPLICANT_SUPPORT_ONLINE_EVALUATION = OnlineEvaluation(evaluators=_ONLINE_ENTRIES)


assert [
    entry.evaluator if isinstance(entry, OnlineEvaluator) else entry
    for entry in _ONLINE_ENTRIES
] == SHARED_EVALUATORS
