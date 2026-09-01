import annotation_studio.main as main


def test_resolve_default_project_name_uses_real_logfire_project_name(monkeypatch) -> None:
    async def fake_info(read_token):
        return {"base_url": "https://logfire-us.pydantic.dev", "organization_name": "acme", "project_name": "acme-support-bot"}

    monkeypatch.setattr(main, "fetch_logfire_project_info", fake_info)

    name = main.resolve_default_project_name("some-token")

    assert name == "acme-support-bot"


def test_resolve_default_project_name_falls_back_when_logfire_call_fails(monkeypatch) -> None:
    async def failing_info(read_token):
        raise RuntimeError("simulated Logfire outage")

    monkeypatch.setattr(main, "fetch_logfire_project_info", failing_info)

    name = main.resolve_default_project_name("some-token")

    assert name == main.DEFAULT_PROJECT_NAME_FALLBACK
