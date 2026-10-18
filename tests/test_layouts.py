import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import get_connection
from app.layouts import router
from app.main import API_PREFIX


@pytest.fixture
def layout_client():
    class Cursor:
        rows = [{"name": "layout_1"}, {"name": "layout_2PT"}]
        query = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, query):
            self.query = query

        async def fetchall(self):
            return self.rows

    cursor = Cursor()

    class Connection:
        def cursor(self, *, row_factory):
            return cursor

    async def fake_connection():
        yield Connection()

    app = FastAPI()
    app.include_router(router, prefix=API_PREFIX)
    app.dependency_overrides[get_connection] = fake_connection
    with TestClient(app) as client:
        yield client, cursor


def test_list_layouts(layout_client):
    client, cursor = layout_client
    response = client.get("/api/v2/layouts/")
    assert response.status_code == 200
    assert response.json() == ["layout_1", "layout_2PT"]
    assert "ORDER BY position, name" in cursor.query


def test_empty_layout_catalogue(layout_client):
    client, cursor = layout_client
    cursor.rows = []
    response = client.get("/api/v2/layouts/")
    assert response.status_code == 200
    assert response.json() == []


def test_database_failure_is_not_hidden(layout_client):
    client, cursor = layout_client

    async def fail(*args, **kwargs):
        raise RuntimeError("Database unavailable")

    cursor.execute = fail
    with pytest.raises(RuntimeError, match="Database unavailable"):
        client.get("/api/v2/layouts/")
