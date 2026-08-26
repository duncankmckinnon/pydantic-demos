import logfire


def configure_logfire(
    service_name: str,
    environment: str = "local",
    send_to_logfire: bool = True,
    token: str | None = None,
) -> None:
    """Configure Logfire with this repo's standard instrumentation.

    Must be called once, before constructing any Agent or FastAPI app, so that
    logfire.configure() runs before the instrument_*() calls register their hooks.

    `token` is passed straight through to logfire.configure(), whose own default is
    None — so passing None here is identical to not passing it at all, and Logfire
    falls back to reading LOGFIRE_TOKEN from the ambient environment itself.
    """
    logfire.configure(
        service_name=service_name,
        environment=environment,
        send_to_logfire=send_to_logfire,
        token=token,
    )
    logfire.instrument_pydantic_ai()
    logfire.instrument_system_metrics()
