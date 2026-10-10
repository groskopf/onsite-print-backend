from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import get_connection
from app.main import api


class FakeCursor:
    async def fetchone(self):
        return ("PostgreSQL test",)


class FakeConnection:
    async def execute(self, query):
        assert query == "SELECT version()"
        return FakeCursor()


async def fake_connection():
    yield FakeConnection()


def test_health_reports_database_version():
    app = FastAPI()
    app.include_router(api)
    app.dependency_overrides[get_connection] = fake_connection
    client = TestClient(app)
    response = client.get("/api/v2/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "PostgreSQL test"}
    assert client.get("/api/health").status_code == 404
