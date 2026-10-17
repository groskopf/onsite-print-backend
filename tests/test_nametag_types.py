import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from app.db import get_connection
from app.nametag_types import router
from app.main import API_PREFIX, request_validation_error_handler

NAMETAG_TYPE = {
    "code": "4786103", "description": "Foldable name tag",
    "height": 86, "width": 103, "folded": True,
}


class FakeCursor:
    def __init__(self):
        self.rows = [NAMETAG_TYPE.copy()]
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
def nametag_type_client():
    cursor = FakeCursor()

    async def fake_connection():
        yield FakeConnection(cursor)

    app = FastAPI()
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.include_router(router, prefix=API_PREFIX)
    app.dependency_overrides[get_connection] = fake_connection
    with TestClient(app) as client:
        yield client, cursor


def test_create_nametag_type(nametag_type_client):
    client, cursor = nametag_type_client
    payload = {**NAMETAG_TYPE, "code": " 4786103 ", "description": " Foldable name tag "}
    response = client.put("/api/v2/nametag_type/", json=payload)
    assert response.status_code == 201
    assert response.json() == NAMETAG_TYPE
    assert cursor.params == ("4786103", "Foldable name tag", 86, 103, True)
    assert "ON CONFLICT (code) DO NOTHING" in cursor.query


def test_fractional_dimensions_and_leading_zero_code(nametag_type_client):
    client, cursor = nametag_type_client
    cursor.rows = [{**NAMETAG_TYPE, "code": "00123", "height": 60.25, "folded": False}]
    response = client.put("/api/v2/nametag_type/", json=cursor.rows[0])
    assert response.status_code == 201
    assert response.json() == cursor.rows[0]
    assert cursor.params == ("00123", "Foldable name tag", 60.25, 103, False)


def test_duplicate_nametag_type(nametag_type_client):
    client, cursor = nametag_type_client
    cursor.rows = []
    response = client.put("/api/v2/nametag_type/", json=NAMETAG_TYPE)
    assert response.status_code == 409
    assert response.json() == {"detail": "Nametag type code already exists"}


def test_get_nametag_type(nametag_type_client):
    client, cursor = nametag_type_client
    response = client.get("/api/v2/nametag_type/4786103")
    assert response.status_code == 200
    assert response.json() == NAMETAG_TYPE
    assert cursor.params == ("4786103",)
    assert "WHERE code = %s" in cursor.query


def test_unknown_nametag_type(nametag_type_client):
    client, cursor = nametag_type_client
    cursor.rows = []
    response = client.get("/api/v2/nametag_type/9999999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Nametag type not found"}


@pytest.mark.parametrize("layouts", [["layout_1", "layout_2PT"], []])
def test_supported_layouts(nametag_type_client, layouts):
    client, cursor = nametag_type_client
    cursor.rows = [{"layouts": layouts}]
    response = client.get("/api/v2/nametag_type/00123/layouts")
    assert response.status_code == 200
    assert response.json() == layouts
    assert cursor.params == ("00123",)
    assert "ORDER BY layout.position, layout.name" in cursor.query


def test_unknown_type_layouts(nametag_type_client):
    client, cursor = nametag_type_client
    cursor.rows = []
    response = client.get("/api/v2/nametag_type/9999999/layouts")
    assert response.status_code == 404
    assert response.json() == {"detail": "Nametag type not found"}


@pytest.mark.parametrize("code", ["abc", "123_4", "123'", "１２３"])
def test_invalid_layout_type_code(nametag_type_client, code):
    client, cursor = nametag_type_client
    response = client.get(f"/api/v2/nametag_type/{code}/layouts")
    assert response.status_code == 422
    assert cursor.query is None


@pytest.mark.parametrize("rows", [[NAMETAG_TYPE], []])
def test_list_nametag_types(nametag_type_client, rows):
    client, cursor = nametag_type_client
    cursor.rows = rows
    response = client.get("/api/v2/nametag_type/")
    assert response.status_code == 200
    assert response.json() == rows
    assert "ORDER BY code" in cursor.query


@pytest.mark.parametrize("field", NAMETAG_TYPE)
def test_missing_field(nametag_type_client, field):
    client, cursor = nametag_type_client
    payload = {key: value for key, value in NAMETAG_TYPE.items() if key != field}
    assert client.put("/api/v2/nametag_type/", json=payload).status_code == 422
    assert cursor.query is None


@pytest.mark.parametrize(("field", "value"), [
    ("code", ""), ("code", "  "), ("code", None), ("code", 4786103),
    ("code", "abc"), ("code", "123_4"), ("code", "123'"), ("code", "１２３"),
    ("description", ""), ("description", "  "), ("description", None),
    ("description", 123),
    ("height", 0), ("height", -1), ("height", None), ("height", "86"),
    ("height", True), ("height", "NaN"), ("height", "Infinity"),
    ("width", 0), ("width", -1), ("width", None), ("width", "103"),
    ("width", False),
    ("folded", None), ("folded", "true"), ("folded", 1),
    ("extra", "not allowed"), ("mirrored", True),
])
def test_invalid_input(nametag_type_client, field, value):
    client, cursor = nametag_type_client
    response = client.put("/api/v2/nametag_type/", json={**NAMETAG_TYPE, field: value})
    assert response.status_code == 422
    assert cursor.query is None


@pytest.mark.parametrize("code", ["abc", "123_4", "123'", "１２３"])
def test_invalid_lookup(nametag_type_client, code):
    client, cursor = nametag_type_client
    assert client.get(f"/api/v2/nametag_type/{code}").status_code == 422
    assert cursor.query is None


@pytest.mark.parametrize("dimension", ["height", "width"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_nonfinite_dimensions(nametag_type_client, dimension, value):
    client, cursor = nametag_type_client
    response = client.put(
        "/api/v2/nametag_type/",
        content=(
            '{"code":"123","description":"Test","folded":false,'
            f'"{dimension}":{value},"{"width" if dimension == "height" else "height"}":10'
            "}"
        ),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert error["type"] == "finite_number"
    assert error["loc"] == ["body", dimension]
    expected_input = {
        "NaN": "nan", "Infinity": "inf", "-Infinity": "-inf", "1e999": "inf",
    }[value]
    assert error["input"] == expected_input
    assert cursor.query is None


def test_nested_nonfinite_extra_field(nametag_type_client):
    client, cursor = nametag_type_client
    response = client.put(
        "/api/v2/nametag_type/",
        content=(
            '{"code":"123","description":"Test","folded":false,'
            '"height":10,"width":20,"extra":{"values":[NaN,Infinity,-Infinity,1.5]}}'
        ),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert error["type"] == "extra_forbidden"
    assert error["input"] == {"values": ["nan", "inf", "-inf", 1.5]}
    assert cursor.query is None


def test_database_failure_is_not_hidden(nametag_type_client):
    client, cursor = nametag_type_client

    async def fail(*args, **kwargs):
        raise RuntimeError("Database unavailable")

    cursor.execute = fail
    with pytest.raises(RuntimeError, match="Database unavailable"):
        client.get("/api/v2/nametag_type/")
