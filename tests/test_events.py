from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import get_connection
from app.events import router
from app.main import API_PREFIX


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.query = None
        self.params = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, query, params=None):
        self.query = query
        self.params = params

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def cursor(self, *, row_factory):
        return self.fake_cursor


@pytest.fixture
def event_client():
    row = {
        "id": 1,
        "name": "Print conference",
        "start_date": date(2026, 10, 9),
        "end_date": date(2026, 10, 10),
    }
    cursor = FakeCursor([row])

    async def fake_connection():
        yield FakeConnection(cursor)

    app = FastAPI()
    app.include_router(router, prefix=API_PREFIX)
    app.dependency_overrides[get_connection] = fake_connection
    with TestClient(app) as client:
        yield client, cursor


def test_create_event(event_client):
    client, cursor = event_client
    payload = {
        "name": "  Print conference  ",
        "start_date": "2026-10-09",
        "end_date": "2026-10-10",
    }
    response = client.put("/api/v2/event/", json=payload)
    assert response.status_code == 201
    assert response.json() == {**payload, "id": 1, "name": "Print conference"}
    assert "INSERT INTO event" in cursor.query
    assert cursor.params == (
        "Print conference",
        date(2026, 10, 9),
        date(2026, 10, 10),
    )


def test_list_events(event_client):
    client, cursor = event_client
    response = client.get("/api/v2/event/")
    assert response.status_code == 200
    assert response.json() == [{
        "id": 1,
        "name": "Print conference",
        "start_date": "2026-10-09",
        "end_date": "2026-10-10",
    }]
    assert "ORDER BY id" in cursor.query


def test_empty_list(event_client):
    client, cursor = event_client
    cursor.rows = []
    assert client.get("/api/v2/event/").json() == []


@pytest.mark.parametrize("changes", [
    {"name": ""},
    {"name": "   "},
    {"start_date": "not-a-date"},
    {"end_date": "2026-10-08"},
    {"extra_field": True},
])
def test_invalid_events_are_rejected(event_client, changes):
    client, cursor = event_client
    payload = {
        "name": "Print conference",
        "start_date": "2026-10-09",
        "end_date": "2026-10-10",
        **changes,
    }
    response = client.put("/api/v2/event/", json=payload)
    assert response.status_code == 422
    assert cursor.query is None


@pytest.mark.parametrize("field", ["name", "start_date", "end_date"])
def test_required_fields(event_client, field):
    client, cursor = event_client
    payload = {
        "name": "Print conference",
        "start_date": "2026-10-09",
        "end_date": "2026-10-10",
    }
    del payload[field]
    assert client.put("/api/v2/event/", json=payload).status_code == 422
    assert cursor.query is None


def test_same_day_event_is_valid(event_client):
    client, cursor = event_client
    cursor.rows[0]["end_date"] = date(2026, 10, 9)
    response = client.put("/api/v2/event/", json={
        "name": "Print conference",
        "start_date": "2026-10-09",
        "end_date": "2026-10-09",
    })
    assert response.status_code == 201


def test_database_errors_are_not_hidden(event_client):
    client, cursor = event_client

    async def fail(*args, **kwargs):
        raise RuntimeError("Database unavailable")

    cursor.execute = fail
    with pytest.raises(RuntimeError, match="Database unavailable"):
        client.get("/api/v2/event/")
