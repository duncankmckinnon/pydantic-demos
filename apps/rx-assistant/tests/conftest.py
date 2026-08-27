import os

import pytest

# Forced (not setdefault) so a developer's real credentials in their shell can never leak
# into a test run. This block runs at conftest import, which pytest loads before any test
# module — and therefore before rx_assistant.main's module-level `app = create_rx_app()`.
os.environ["PYDANTIC_AI_GATEWAY_API_KEY"] = "pylf_v1_us_test-key"
os.environ["LOGFIRE_TOKEN"] = "test-token"
# Makes create_rx_app() default to offline, including the module-level call that runs
# at import/collection time before any fixture could intervene.
os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"
# Never dialed: create_rx_app() only opens a pool when the caller doesn't pass `deps=`
# directly (see Task 7), which every test in this suite does.
os.environ["DATABASE_URL"] = "postgresql://test:test@localhost/test"

import logfire  # noqa: E402
import pydantic_evals.online  # noqa: E402

# The rx-assistant agent has an online-eval capability (rx_assistant.evals.online.
# RX_ASSISTANT_ONLINE_EVALUATION) whose judge evaluator makes a real Gateway call and opens
# its own database connection. Disabled globally here — same reasoning as the Gateway/Logfire
# env vars above — so no test triggers a real model call or db connection in the background
# just by hitting /api/chat.
pydantic_evals.online.configure(enabled=False)


@pytest.fixture(autouse=True, scope="session")
def _configure_logfire_for_tests() -> None:
    logfire.configure(send_to_logfire=False)


@pytest.fixture(autouse=True)
def _clear_model_cache() -> None:
    """rx_assistant.main._MODEL_CACHE is module-level and outlives create_rx_app(), so
    clear it between tests — otherwise a model cached under one test's monkeypatched
    get_model would silently be reused by the next test's supposedly fresh monkeypatch."""
    import rx_assistant.main

    rx_assistant.main._MODEL_CACHE.clear()
