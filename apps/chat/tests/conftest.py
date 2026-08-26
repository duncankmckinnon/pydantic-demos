import os

import logfire
import pytest

# gateway_provider() validates the key's shape via regex (pylf_v<n>_<region>_...) even
# though no network call happens at construction time — an arbitrary string like
# "test-key" raises a UserError before a test ever gets to run. See Task 4's report.
os.environ.setdefault("PYDANTIC_AI_GATEWAY_API_KEY", "pylf_v1_us_test-key")
os.environ.setdefault("LOGFIRE_TOKEN", "test-token")


@pytest.fixture(autouse=True, scope="session")
def _configure_logfire_for_tests() -> None:
    logfire.configure(send_to_logfire=False)
