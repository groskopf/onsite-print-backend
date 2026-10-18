import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import get_connection
from app.main import API_PREFIX
from app.printers import router


class FakeCursor:
    def __init__(self):
        self.rows = [{"code": "ALGCSOIO9O_A", "label": "A", "description": "Virtual printer"}]
        self.query = None
        self.params = None
        self.queries = []
        self.results = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, query, params=None):
        self.query = query
        self.params = params
        self.queries.append((query, params))

    async def fetchone(self):
        if self.results is not None:
            return self.results.pop(0)
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def cursor(self, *, row_factory):
        return self.fake_cursor


@pytest.fixture
def printer_client():
    cursor = FakeCursor()

    async def fake_connection():
        yield FakeConnection(cursor)

    app = FastAPI()
    app.include_router(router, prefix=API_PREFIX)
    app.dependency_overrides[get_connection] = fake_connection
    with TestClient(app) as client:
        yield client, cursor


@pytest.mark.parametrize(("code", "label"), [
    ("ALGCSOIO9O_A", "A"), ("COIC1PEJ8J_10", "10"), ("Pey3NazUi3_B", "B"),
])
def test_create_printer_derives_label(printer_client, code, label):
    client, cursor = printer_client
    cursor.rows = [{"code": code, "label": label, "description": "Test printer"}]
    response = client.post("/api/v2/printers/", json={
        "code": f"  {code}  ", "description": "  Test printer  ",
    })
    assert response.status_code == 201
    assert response.json() == {"code": code, "label": label, "description": "Test printer"}
    assert cursor.params == (code, label, "Test printer")
    assert "ON CONFLICT (code) DO NOTHING" in cursor.query


def test_duplicate_printer(printer_client):
    client, cursor = printer_client
    cursor.rows = []
    response = client.post("/api/v2/printers/", json={
        "code": "ALGCSOIO9O_A", "description": "Duplicate",
    })
    assert response.status_code == 409
    assert response.json() == {"detail": "Printer code already exists"}


def test_get_printer_by_code(printer_client):
    client, cursor = printer_client
    response = client.get("/api/v2/printers/ALGCSOIO9O_A")
    assert response.status_code == 200
    assert response.json() == cursor.rows[0]
    assert cursor.params == ("ALGCSOIO9O_A",)
    assert "WHERE code = %s" in cursor.query


def test_unknown_printer(printer_client):
    client, cursor = printer_client
    cursor.rows = []
    response = client.get("/api/v2/printers/UNKNOWN_1")
    assert response.status_code == 404
    assert response.json() == {"detail": "Printer not found"}


def test_list_printers(printer_client):
    client, cursor = printer_client
    response = client.get("/api/v2/printers/")
    assert response.status_code == 200
    assert response.json() == cursor.rows
    assert "ORDER BY code" in cursor.query


def test_empty_printer_list(printer_client):
    client, cursor = printer_client
    cursor.rows = []
    assert client.get("/api/v2/printers/").json() == []


@pytest.mark.parametrize("payload", [
    {}, {"code": None}, {"code": 123}, {"code": ""}, {"code": "  "},
    {"code": "NO-SUFFIX"}, {"code": "_A"}, {"code": "ABC_"},
    {"code": "ABC_A_B"}, {"code": "ABC_'"},
    {"code": "ABC_A", "label": "custom"},
])
def test_invalid_printer_is_rejected(printer_client, payload):
    client, cursor = printer_client
    assert client.post(
        "/api/v2/printers/", json={"description": "Test printer", **payload},
    ).status_code == 422
    assert cursor.query is None


def test_invalid_lookup_code(printer_client):
    client, cursor = printer_client
    assert client.get("/api/v2/printers/invalid").status_code == 422
    assert cursor.query is None


@pytest.mark.parametrize("description", [None, "", "   ", 123, [], {}])
def test_invalid_description(printer_client, description):
    client, cursor = printer_client
    response = client.post("/api/v2/printers/", json={
        "code": "NEWPRINTER_13", "description": description,
    })
    assert response.status_code == 422
    assert cursor.query is None


def test_missing_description(printer_client):
    client, cursor = printer_client
    assert client.post("/api/v2/printers/", json={"code": "NEWPRINTER_13"}).status_code == 422
    assert cursor.query is None


def test_legacy_printer_without_description(printer_client):
    client, cursor = printer_client
    cursor.rows[0]["description"] = None
    response = client.get("/api/v2/printers/ALGCSOIO9O_A")
    assert response.status_code == 200
    assert response.json()["description"] is None


def test_database_failure_is_not_hidden(printer_client):
    client, cursor = printer_client

    async def fail(*args, **kwargs):
        raise RuntimeError("Database unavailable")

    cursor.execute = fail
    with pytest.raises(RuntimeError, match="Database unavailable"):
        client.get("/api/v2/printers/")


URL = "/api/v2/printers/ALGCSOIO9O_A"


def test_update_printer_description(printer_client):
    client, cursor = printer_client
    cursor.rows[0]["description"] = "Updated"
    response = client.put(URL, json={"description": "  Updated  "})
    assert response.status_code == 200
    assert response.json() == {"code": "ALGCSOIO9O_A", "label": "A", "description": "Updated"}
    assert cursor.query.startswith("UPDATE printer SET description = %s WHERE code = %s")
    assert cursor.params == ("Updated", "ALGCSOIO9O_A")


@pytest.mark.parametrize("payload", [
    {}, {"description": None}, {"description": "  "},
    {"description": "Updated", "code": "ALGCSOIO9O_A"},
    {"description": "Updated", "label": "B"},
])
def test_invalid_printer_update(printer_client, payload):
    client, cursor = printer_client
    assert client.put(URL, json=payload).status_code == 422
    assert cursor.queries == []


def test_delete_printer(printer_client):
    client, cursor = printer_client
    row = cursor.rows[0]
    cursor.results = [{"?column?": 1}, None, row]
    response = client.delete(URL)
    assert response.status_code == 200
    assert response.json() == row
    queries = [query for query, _ in cursor.queries]
    assert "FOR UPDATE" in queries[0]
    assert "FROM booking WHERE printer_code" in queries[1]
    assert queries[2].startswith("DELETE FROM printer")
    assert all(params == ("ALGCSOIO9O_A",) for _, params in cursor.queries)


def test_delete_printer_with_bookings(printer_client):
    client, cursor = printer_client
    cursor.results = [{"?column?": 1}, {"?column?": 1}]
    response = client.delete(URL)
    assert response.status_code == 409
    assert response.json() == {"detail": "Printer has bookings"}
    assert not any(query.startswith("DELETE") for query, _ in cursor.queries)


@pytest.mark.parametrize("method", ["put", "delete"])
def test_missing_printer(printer_client, method):
    client, cursor = printer_client
    cursor.rows = []
    kwargs = {"json": {"description": "Updated"}} if method == "put" else {}
    response = getattr(client, method)("/api/v2/printers/UNKNOWN_1", **kwargs)
    assert response.status_code == 404
    assert response.json() == {"detail": "Printer not found"}
    assert len(cursor.queries) == 1


@pytest.mark.parametrize("method", ["put", "delete"])
def test_invalid_printer_code_in_path(printer_client, method):
    client, cursor = printer_client
    kwargs = {"json": {"description": "Updated"}} if method == "put" else {}
    assert getattr(client, method)("/api/v2/printers/invalid", **kwargs).status_code == 422
    assert cursor.queries == []
