from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Contains, IsInstance


voice_dataset = Dataset(
    name="audio_demo_voice_responses",
    cases=[
        Case(
            name="payment_amount",
            inputs="Look up sample payments for account 123. What is the most recent payment amount?",
            evaluators=[Contains(value="412", case_sensitive=False)],
        ),
        Case(
            name="autopay_status",
            inputs="Look up sample payments for account 456. Is autopay enabled?",
            evaluators=[Contains(value="autopay", case_sensitive=False)],
        ),
        Case(
            name="missing_account",
            inputs="Can you look up my payment history?",
            evaluators=[Contains(value="account", case_sensitive=False)],
        ),
    ],
    evaluators=[IsInstance(type_name="str")],
)
