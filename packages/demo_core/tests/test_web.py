from fastapi import Request
from fastapi.testclient import TestClient

from demo_core.web import create_app


def test_health_route_returns_ok() -> None:
    app = create_app(title="Test App")
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unhandled_error_returns_consistent_json() -> None:
    app = create_app(title="Test App")

    @app.get("/boom")
    async def boom(request: Request) -> None:
        raise RuntimeError("kaboom")

    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {"error": "internal_server_error"}
