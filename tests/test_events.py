from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import events
from app.db import get_connection
from app.events import router
from app.main import API_PREFIX

EMPTY_ADDRESS = {"street": None, "zip": None, "town": None, "country": None}


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.query = None
        self.params = None
        self.queries = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, query, params=None):
        self.query = query
        self.params = params
        self.queries.append((query, params))

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
        "code": "ABCDEFGHIJKLMNOP",
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


def test_create_event(event_client, monkeypatch):
    monkeypatch.setattr(events, "generate_code", lambda: "ABCDEFGHIJKLMNOP")
    client, cursor = event_client
    payload = {
        "name": "  Print conference  ",
        "start_date": "2026-10-09",
        "end_date": "2026-10-10",
    }
    response = client.post("/api/v2/events/", json=payload)
    assert response.status_code == 201
    assert response.json() == {
        **payload, **EMPTY_ADDRESS, "id": 1, "code": "ABCDEFGHIJKLMNOP", "name": "Print conference",
    }
    assert "INSERT INTO event" in cursor.query
    assert cursor.params[0] == "ABCDEFGHIJKLMNOP"
    assert cursor.params[1:] == (
        "Print conference",
        date(2026, 10, 9),
        date(2026, 10, 10),
        None, None, None, None,
    )


def test_list_events(event_client):
    client, cursor = event_client
    response = client.get("/api/v2/events/")
    assert response.status_code == 200
    assert response.json() == [{
        "id": 1,
        "code": "ABCDEFGHIJKLMNOP",
        "name": "Print conference",
        "start_date": "2026-10-09",
        "end_date": "2026-10-10",
        **EMPTY_ADDRESS,
    }]
    assert "ORDER BY id" in cursor.query


def test_empty_list(event_client):
    client, cursor = event_client
    cursor.rows = []
    assert client.get("/api/v2/events/").json() == []


@pytest.mark.parametrize("address", [
    {"street": "  Main Street 12  ", "zip": "  0123  ",
     "town": "  Copenhagen  ", "country": "  Denmark  "},
    {"zip": "SW1A 1AA"},
    EMPTY_ADDRESS,
])
def test_optional_address_is_stored_and_returned(event_client, address):
    client, cursor = event_client
    expected = {
        **EMPTY_ADDRESS,
        **{key: value.strip() if value is not None else None
           for key, value in address.items()},
    }
    cursor.rows[0].update(expected)
    response = client.post("/api/v2/events/", json={
        "name": "Print conference",
        "start_date": "2026-10-09",
        "end_date": "2026-10-10",
        **address,
    })
    assert response.status_code == 201
    assert {key: response.json()[key] for key in EMPTY_ADDRESS} == expected
    assert cursor.params[4:] == tuple(expected.values())
    response = client.get("/api/v2/events/")
    assert {key: response.json()[0][key] for key in EMPTY_ADDRESS} == expected


@pytest.mark.parametrize("field", EMPTY_ADDRESS)
@pytest.mark.parametrize("value", ["", "   ", 123, [], {}])
def test_invalid_address_fields_are_rejected(event_client, field, value):
    client, cursor = event_client
    response = client.post("/api/v2/events/", json={
        "name": "Print conference",
        "start_date": "2026-10-09",
        "end_date": "2026-10-10",
        field: value,
    })
    assert response.status_code == 422
    assert cursor.query is None


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
    response = client.post("/api/v2/events/", json=payload)
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
    assert client.post("/api/v2/events/", json=payload).status_code == 422
    assert cursor.query is None


def test_same_day_event_is_valid(event_client):
    client, cursor = event_client
    cursor.rows[0]["end_date"] = date(2026, 10, 9)
    response = client.post("/api/v2/events/", json={
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
        client.get("/api/v2/events/")


CODE = "ABCDEFGHIJKLMNOP"
URL = f"{API_PREFIX}/events/{CODE}"
UPDATE = {
    "name": "  Renamed conference  ",
    "start_date": "2026-10-11",
    "end_date": "2026-10-12",
    "town": "Aarhus",
}


def test_get_event(event_client):
    client, cursor = event_client
    response = client.get(URL)
    assert response.status_code == 200
    assert response.json()["code"] == CODE
    assert "WHERE code = %s" in cursor.query
    assert cursor.params == (CODE,)


def test_update_event(event_client):
    client, cursor = event_client
    cursor.rows[0].update(name="Renamed conference", start_date=date(2026, 10, 11),
                          end_date=date(2026, 10, 12), town="Aarhus")
    response = client.put(URL, json=UPDATE)
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed conference"
    assert response.json()["code"] == CODE
    assert cursor.query.lstrip().startswith("UPDATE event")
    assert "code = %s" not in cursor.query.split("WHERE")[0]
    assert cursor.params == (
        "Renamed conference", date(2026, 10, 11), date(2026, 10, 12),
        None, None, "Aarhus", None, CODE,
    )


def test_delete_event_deletes_its_bookings(event_client):
    client, cursor = event_client
    response = client.delete(URL)
    assert response.status_code == 200
    assert response.json()["code"] == CODE
    queries = [query for query, _ in cursor.queries]
    assert "FOR UPDATE" in queries[0]
    assert queries[1].startswith("DELETE FROM booking WHERE event_code")
    assert queries[2].startswith("DELETE FROM event")
    assert all(params == (CODE,) for _, params in cursor.queries)


@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_missing_event(event_client, method):
    client, cursor = event_client
    cursor.rows = []
    kwargs = {"json": UPDATE} if method == "put" else {}
    response = getattr(client, method)(URL, **kwargs)
    assert response.status_code == 404
    assert response.json() == {"detail": "Event not found"}
    assert len(cursor.queries) == 1


@pytest.mark.parametrize("method", ["get", "put", "delete"])
@pytest.mark.parametrize("code", ["abcdefghijklmnop", "ABCDEFGHIJKLMNO", "ABCDEFGHIJKLMNO1", "ABCDEFGHIJKLMNOPQ", "1"])
def test_invalid_event_code(event_client, method, code):
    client, cursor = event_client
    kwargs = {"json": UPDATE} if method == "put" else {}
    response = getattr(client, method)(f"{API_PREFIX}/events/{code}", **kwargs)
    assert response.status_code == 422
    assert cursor.queries == []


@pytest.mark.parametrize("changes", [{"end_date": "2026-10-10"}, {"code": "QRSTUVWXYZ234567"}, {"id": 2}])
def test_invalid_event_update_is_rejected(event_client, changes):
    client, cursor = event_client
    assert client.put(URL, json={**UPDATE, **changes}).status_code == 422
    assert cursor.queries == []
