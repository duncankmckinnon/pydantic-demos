from pydantic_evals.online import OnlineEvaluator
from pydantic_evals.online_capability import OnlineEvaluation

from rx_assistant.evals.db_judge import rx_assistant_db_judge

# Attached to the rx-assistant agent (see rx_assistant.main) so a sample of real /api/chat
# calls are scored in the background; results show up as gen_ai.evaluation.result events in
# Logfire's Live Evaluations view. rx_assistant_db_judge makes a real model call (plus its own
# database lookups), so it's sampled at 20% rather than run on every message — matching
# chat.evals.online's sample rate for its judges.
RX_ASSISTANT_ONLINE_EVALUATION = OnlineEvaluation(
    evaluators=[
        OnlineEvaluator(evaluator=rx_assistant_db_judge, sample_rate=0.5),
    ]
)
