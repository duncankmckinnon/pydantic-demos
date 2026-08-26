"""Chat demo application.

Loads this app's own .env here, at package import, so it is in place before *any*
submodule constructs a Settings object — that covers both entrypoints: the FastAPI app
(`chat.main`) and the manual eval runner (`chat.evals.run`), which does not import
`chat.main`. The path is resolved relative to this file rather than the process CWD so
`uv run --package chat ...` works from the repo root. override=False keeps real
environment variables (e.g. docker-compose's env_file:) ahead of the .env file.
"""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
