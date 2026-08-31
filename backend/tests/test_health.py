"""
Module 0 definition of done: the FastAPI app returns {"status": "ok"}
on /health. Kept as a real test rather than a manual check so later
modules can't silently break it.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
