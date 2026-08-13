from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_app_metadata():
    assert app.title == "BREACH API"
    assert app.version == "1.0.0"


def test_health_endpoint_reports_degraded_without_db():
    # No `with TestClient(app)` context here, so the lifespan (and its DB
    # connect) never runs — health_check() must degrade gracefully, not crash.
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"] == "disconnected"


def test_document_and_query_routers_mounted():
    # openapi()["paths"] is the stable, public way to introspect mounted
    # routes — unlike app.routes, whose internal shape varies across
    # Starlette versions (e.g. included routers vs. flattened Route objects).
    paths = app.openapi()["paths"]
    assert "/api/documents/" in paths
    assert "/api/query/" in paths
