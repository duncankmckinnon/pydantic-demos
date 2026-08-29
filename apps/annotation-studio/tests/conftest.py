import os
import tempfile

_db_fd, _db_path = tempfile.mkstemp(suffix=".sqlite3")
os.close(_db_fd)

# Forced (not setdefault) so a developer's real credentials in their shell can never leak
# into a test run. This runs at conftest import, before any test module.
os.environ["LOGFIRE_TOKEN"] = "test-token"
os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"
os.environ["RX_ASSISTANT_LOGFIRE_READ_TOKEN"] = "test-read-token"
os.environ["RX_ASSISTANT_LOGFIRE_WRITE_TOKEN"] = "test-write-token"
os.environ["ANNOTATION_STUDIO_DATABASE_PATH"] = _db_path

import logfire  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def _configure_logfire_for_tests() -> None:
    logfire.configure(send_to_logfire=False)
