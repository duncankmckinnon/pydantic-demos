import os

os.environ["PYDANTIC_AI_GATEWAY_API_KEY"] = "pylf_v1_us_test-key"
os.environ["LOGFIRE_TOKEN"] = "test-token"
os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

import logfire
import pydantic_evals.online
import pytest
from pydantic_ai import models

pydantic_evals.online.configure(enabled=False)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)
    logfire.instrument_pydantic_ai()
