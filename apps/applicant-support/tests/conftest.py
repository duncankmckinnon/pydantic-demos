import os

import pytest

os.environ["PYDANTIC_AI_GATEWAY_API_KEY"] = "pylf_v1_us_test-key"
os.environ["LOGFIRE_TOKEN"] = "test-token"
os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"
os.environ["APPLICANT_DB_PATH"] = "/tmp/applicant-support-tests.sqlite3"

import logfire  # noqa: E402
import pydantic_evals.online  # noqa: E402

pydantic_evals.online.configure(enabled=False)


@pytest.fixture(autouse=True, scope="session")
def _configure_logfire_for_tests() -> None:
    logfire.configure(send_to_logfire=False)


@pytest.fixture(autouse=True)
def _clear_app_state() -> None:
    import applicant_support.main

    applicant_support.main._MODEL_CACHE.clear()
    applicant_support.main._SESSIONS.clear()
    applicant_support.main._JOB_REGISTRY.reset()
    applicant_support.main._EXPERIMENT_CONTEXTS.clear()
