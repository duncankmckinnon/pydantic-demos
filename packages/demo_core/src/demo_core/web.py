import logfire
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def create_app(title: str) -> FastAPI:
    """Build a FastAPI app with this repo's standard health check and error handling.

    Call demo_core.logfire_setup.configure_logfire() before this, so
    logfire.instrument_fastapi() below has an already-configured client to report to.
    """
    app = FastAPI(title=title)
    logfire.instrument_fastapi(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logfire.exception(
            "Unhandled error on {method} {path}",
            method=request.method,
            path=request.url.path,
        )
        return JSONResponse(status_code=500, content={"error": "internal_server_error"})

    return app
