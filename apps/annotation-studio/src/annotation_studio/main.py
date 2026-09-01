import asyncio
import os
import sqlite3
from pathlib import Path

import logfire
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from demo_core.logfire_setup import configure_logfire
from demo_core.settings import LogfireSettings
from demo_core.web import create_app

from annotation_studio import db
from annotation_studio.logfire_client import fetch_logfire_project_info
from annotation_studio.logfire_writer import AnnotationWriter
from annotation_studio.routes import register_routes
from annotation_studio.settings import AppSettings, SourceSettings

_STATIC_DIST = Path(__file__).parent / "static" / "dist"

DEFAULT_PROJECT_NAME_FALLBACK = "My Project"


def resolve_default_project_name(read_token: str) -> str:
    """Names the app's one local project after the real Logfire project `read_token` points
    at, so a fresh install never shows a hardcoded or made-up name. Falls back to a generic
    name if the Logfire lookup fails (bad token, network hiccup) — a flaky call at startup
    must not crash the whole app, since there's no UI to rename the project afterwards."""
    try:
        info = asyncio.run(fetch_logfire_project_info(read_token))
        return info["project_name"]
    except Exception:
        logfire.warning("Could not fetch Logfire project info at startup; using a generic project name")
        return DEFAULT_PROJECT_NAME_FALLBACK


def create_annotation_studio_app(
    send_to_logfire: bool | None = None,
    connection: sqlite3.Connection | None = None,
    writer: AnnotationWriter | None = None,
    default_project_name: str | None = None,
) -> FastAPI:
    if send_to_logfire is None:
        # Lets the test suite (see tests/conftest.py) force offline mode before this
        # module's own `app = create_annotation_studio_app()` line runs at import time.
        send_to_logfire = os.environ.get("LOGFIRE_SEND_TO_LOGFIRE", "true").lower() != "false"

    logfire_settings = LogfireSettings()
    configure_logfire("annotation-studio", send_to_logfire=send_to_logfire, token=logfire_settings.token)
    app = create_app(title="Annotation Studio")

    app_settings = AppSettings()
    source_settings = SourceSettings()

    conn = connection if connection is not None else db.get_connection(app_settings.database_path)
    db.init_db(conn)
    if not db.list_projects(conn):
        # Only resolved when actually seeding — an already-seeded install must not make a
        # Logfire call (and `default_project_name` injection lets tests skip it too).
        name = default_project_name if default_project_name is not None else resolve_default_project_name(source_settings.read_token)
        db.seed_default_project(conn, name)

    # `writer` injection lets tests supply a fake without ever calling
    # logfire.configure(local=True, ...) in the test suite.
    active_writer = writer if writer is not None else AnnotationWriter(source_settings.write_token)

    register_routes(app, conn, source_settings, app_settings, active_writer)

    # Serve the built React SPA. /assets holds Vite's hashed JS/CSS; the catch-all below
    # returns index.html for every other non-API path so React Router's client-side routes
    # (e.g. /projects/5) work on a hard refresh, not just on in-app navigation.
    if _STATIC_DIST.exists():
        app.mount("/assets", StaticFiles(directory=str(_STATIC_DIST / "assets")), name="frontend-assets")

        @app.get("/{full_path:path}")
        async def serve_frontend(full_path: str) -> FileResponse:
            # An unmatched /api/* path is a genuine 404 (mistyped or nonexistent route),
            # not a client-side route the SPA should handle — don't mask it with index.html.
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404)
            return FileResponse(_STATIC_DIST / "index.html")

    return app


app = create_annotation_studio_app()
