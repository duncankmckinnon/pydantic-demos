import os

import pytest

# Forced (not setdefault) so a developer's real credentials in their shell can never leak
# into a test run. This block runs at conftest import, which pytest loads before any test
# module — and therefore before chat.main's module-level `app = create_chat_app()`.
#
# gateway_provider() validates the key's shape via regex (pylf_v<n>_<region>_...) even
# though no network call happens at construction time — an arbitrary string like
# "test-key" raises a UserError before a test ever gets to run. See Task 4's report.
os.environ["PYDANTIC_AI_GATEWAY_API_KEY"] = "pylf_v1_us_test-key"
os.environ["LOGFIRE_TOKEN"] = "test-token"
# Makes create_chat_app() default to offline, including the module-level call that runs
# at import/collection time before any fixture could intervene.
os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

import logfire  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def _configure_logfire_for_tests() -> None:
    logfire.configure(send_to_logfire=False)


@pytest.fixture(autouse=True)
def _clear_model_cache() -> None:
    """chat.main._MODEL_CACHE is module-level and outlives create_chat_app(), so clear it
    between tests — otherwise a model cached under one test's monkeypatched get_model
    would silently be reused by the next test's supposedly fresh monkeypatch."""
    import chat.main

    chat.main._MODEL_CACHE.clear()
