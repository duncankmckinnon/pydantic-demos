import logfire


def configure_logfire(
    service_name: str,
    environment: str = "local",
    send_to_logfire: bool = True,
) -> None:
    """Configure Logfire with this repo's standard instrumentation.

    Must be called once, before constructing any Agent or FastAPI app, so that
    logfire.configure() runs before the instrument_*() calls register their hooks.
    """
    logfire.configure(
        service_name=service_name,
        environment=environment,
        send_to_logfire=send_to_logfire,
    )
    logfire.instrument_pydantic_ai()
    logfire.instrument_system_metrics()
