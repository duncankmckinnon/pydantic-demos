"""Annotation Studio demo application.

Loads this app's own .env here, at package import, so it is in place before any
submodule constructs a Settings object. override=False keeps real environment
variables (e.g. docker-compose's env_file:) ahead of the .env file.
"""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
