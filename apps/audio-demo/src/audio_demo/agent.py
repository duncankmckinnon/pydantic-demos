"""The voice agent from audio-demo, with its original payment-history tool."""

from pydantic_ai import Agent

MODEL_NAME = "gpt-realtime"


def get_payment_history(account_id: str) -> str:
    """Look up sample recent payments on a borrower's loan account."""
    return (
        f"Account {account_id}: last three payments were $412.18 on Aug 1, "
        "$412.18 on Jul 1, and $412.18 on Jun 1. Autopay is enabled."
    )


def build_agent() -> Agent:
    # Realtime models are supplied to agent.realtime(), not Agent's text-model argument.
    # Configure Logfire before calling this, just as in the other demos.
    return Agent(
        name="audio_agent",
        instructions=(
            "You are a friendly voice assistant. Keep replies short and conversational. "
            "You can look up sample loan payments with get_payment_history. Ask for the "
            "account ID if it is missing. Explain that these are sample records, and do "
            "not claim to change payments or accounts."
        ),
        tools=[get_payment_history],
    )
