from pydantic_evals import Case

from chat.evals.dataset import chat_eval_dataset
from chat.evals.efficiency import ResponseEfficiency
from demo_core.evals import HarnessJudge


def test_dataset_has_expected_cases_and_evaluators() -> None:
    assert len(chat_eval_dataset.cases) == 2
    assert all(isinstance(case, Case) for case in chat_eval_dataset.cases)
    assert any(isinstance(ev, HarnessJudge) for ev in chat_eval_dataset.evaluators)
    assert any(isinstance(ev, ResponseEfficiency) for ev in chat_eval_dataset.evaluators)
