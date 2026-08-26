import logfire
import pytest


@pytest.fixture(autouse=True, scope="session")
def _configure_logfire_for_tests() -> None:
    """Run Logfire in local-only mode for the whole test session.

    Without this, logfire.instrument_fastapi() in demo_core.web runs against an
    unconfigured Logfire client during tests.
    """
    logfire.configure(send_to_logfire=False)
