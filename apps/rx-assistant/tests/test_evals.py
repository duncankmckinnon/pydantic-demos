from pydantic_evals import Case

from demo_core.evals import HarnessJudge
from rx_assistant.evals.dataset import rx_assistant_eval_dataset


def test_dataset_has_expected_cases_and_evaluators() -> None:
    assert len(rx_assistant_eval_dataset.cases) == 2
    assert all(isinstance(case, Case) for case in rx_assistant_eval_dataset.cases)
    assert any(isinstance(ev, HarnessJudge) for ev in rx_assistant_eval_dataset.evaluators)
