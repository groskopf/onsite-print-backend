import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import INITIAL_SHEET_LAYOUTS, INITIAL_SHEET_TYPES, get_connection
from app.endpoint_sheet_types import router

EXPECTED = {
    "code": "456090",
    "description": "A4 sheet with 60x90 mm labels",
    "height": 60.0,
    "width": 90.0,
    "folded": False,
}


@pytest.fixture
def setup():
    class Cursor:
        rows = [EXPECTED]
        row = EXPECTED
        queries = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, query, params=None):
            self.queries.append((query, params))

        async def fetchall(self):
            return self.rows

        async def fetchone(self):
            return self.row

    cursor = Cursor()

    class Connection:
        def cursor(self, *, row_factory):
            return cursor

    async def connection():
        yield Connection()

    app = FastAPI()
    app.include_router(router, prefix="/api/v2")
    app.dependency_overrides[get_connection] = connection
    with TestClient(app) as client:
        yield client, cursor


def test_static_types_only_advertise_implemented_format():
    assert INITIAL_SHEET_TYPES == (("456090", EXPECTED["description"], 60, 90, False),)
    assert INITIAL_SHEET_LAYOUTS == (("456090", "layout_1"),)


def test_reference_endpoints(setup):
    client, cursor = setup
    assert client.get("/api/v2/sheet_types/").json() == [EXPECTED]
    assert "ORDER BY code" in cursor.queries[-1][0]
    assert client.get("/api/v2/sheet_types/456090").json() == EXPECTED
    assert cursor.queries[-1][1] == ("456090",)
    cursor.row = {"layouts": ["layout_1"]}
    assert client.get("/api/v2/sheet_types/456090/layouts").json() == ["layout_1"]
    assert "ORDER BY layout.position, layout.name" in cursor.queries[-1][0]
    cursor.row = {"layouts": []}
    assert client.get("/api/v2/sheet_types/456090/layouts").json() == []
    cursor.rows = []
    assert client.get("/api/v2/sheet_types/").json() == []


@pytest.mark.parametrize("suffix", ["", "/layouts"])
def test_unknown_or_invalid_sheet_types(setup, suffix):
    client, cursor = setup
    cursor.row = None
    response = client.get("/api/v2/sheet_types/999" + suffix)
    assert response.status_code == 404
    assert response.json() == {"detail": "Sheet type not found"}
    assert client.get("/api/v2/sheet_types/invalid" + suffix).status_code == 422


def test_reference_endpoints_are_read_only(setup):
    client, cursor = setup
    assert client.post("/api/v2/sheet_types/", json=EXPECTED).status_code == 405
    assert client.put("/api/v2/sheet_types/456090", json=EXPECTED).status_code == 405
    assert client.delete("/api/v2/sheet_types/456090").status_code == 405


def test_database_errors_are_not_hidden(setup):
    client, cursor = setup

    async def fail(*args, **kwargs):
        raise RuntimeError("Database unavailable")

    cursor.execute = fail
    with pytest.raises(RuntimeError, match="Database unavailable"):
        client.get("/api/v2/sheet_types/")
