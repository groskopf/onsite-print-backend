import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from app.db import get_connection
from app.label_types import router
from app.main import API_PREFIX, request_validation_error_handler

LABEL_TYPE = {
    "code": "4786103", "description": "Foldable name tag",
    "height": 86, "width": 103, "mirrored": True,
}


class FakeCursor:
    def __init__(self):
        self.rows = [LABEL_TYPE.copy()]
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
def label_type_client():
    cursor = FakeCursor()

    async def fake_connection():
        yield FakeConnection(cursor)

    app = FastAPI()
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.include_router(router, prefix=API_PREFIX)
    app.dependency_overrides[get_connection] = fake_connection
    with TestClient(app) as client:
        yield client, cursor


def test_create_label_type(label_type_client):
    client, cursor = label_type_client
    payload = {**LABEL_TYPE, "code": " 4786103 ", "description": " Foldable name tag "}
    response = client.put("/api/v2/label_type/", json=payload)
    assert response.status_code == 201
    assert response.json() == LABEL_TYPE
    assert cursor.params == ("4786103", "Foldable name tag", 86, 103, True)
    assert "ON CONFLICT (code) DO NOTHING" in cursor.query


def test_fractional_dimensions_and_leading_zero_code(label_type_client):
    client, cursor = label_type_client
    cursor.rows = [{**LABEL_TYPE, "code": "00123", "height": 60.25, "mirrored": False}]
    response = client.put("/api/v2/label_type/", json=cursor.rows[0])
    assert response.status_code == 201
    assert response.json() == cursor.rows[0]
    assert cursor.params == ("00123", "Foldable name tag", 60.25, 103, False)


def test_duplicate_label_type(label_type_client):
    client, cursor = label_type_client
    cursor.rows = []
    response = client.put("/api/v2/label_type/", json=LABEL_TYPE)
    assert response.status_code == 409
    assert response.json() == {"detail": "Label type code already exists"}


def test_get_label_type(label_type_client):
    client, cursor = label_type_client
    response = client.get("/api/v2/label_type/4786103")
    assert response.status_code == 200
    assert response.json() == LABEL_TYPE
    assert cursor.params == ("4786103",)
    assert "WHERE code = %s" in cursor.query


def test_unknown_label_type(label_type_client):
    client, cursor = label_type_client
    cursor.rows = []
    response = client.get("/api/v2/label_type/9999999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Label type not found"}


@pytest.mark.parametrize("rows", [[LABEL_TYPE], []])
def test_list_label_types(label_type_client, rows):
    client, cursor = label_type_client
    cursor.rows = rows
    response = client.get("/api/v2/label_type/")
    assert response.status_code == 200
    assert response.json() == rows
    assert "ORDER BY code" in cursor.query


@pytest.mark.parametrize("field", LABEL_TYPE)
def test_missing_field(label_type_client, field):
    client, cursor = label_type_client
    payload = {key: value for key, value in LABEL_TYPE.items() if key != field}
    assert client.put("/api/v2/label_type/", json=payload).status_code == 422
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
    ("mirrored", None), ("mirrored", "true"), ("mirrored", 1),
    ("extra", "not allowed"),
])
def test_invalid_input(label_type_client, field, value):
    client, cursor = label_type_client
    response = client.put("/api/v2/label_type/", json={**LABEL_TYPE, field: value})
    assert response.status_code == 422
    assert cursor.query is None


@pytest.mark.parametrize("code", ["abc", "123_4", "123'", "１２３"])
def test_invalid_lookup(label_type_client, code):
    client, cursor = label_type_client
    assert client.get(f"/api/v2/label_type/{code}").status_code == 422
    assert cursor.query is None


@pytest.mark.parametrize("dimension", ["height", "width"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_nonfinite_dimensions(label_type_client, dimension, value):
    client, cursor = label_type_client
    response = client.put(
        "/api/v2/label_type/",
        content=(
            '{"code":"123","description":"Test","mirrored":false,'
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


def test_nested_nonfinite_extra_field(label_type_client):
    client, cursor = label_type_client
    response = client.put(
        "/api/v2/label_type/",
        content=(
            '{"code":"123","description":"Test","mirrored":false,'
            '"height":10,"width":20,"extra":{"values":[NaN,Infinity,-Infinity,1.5]}}'
        ),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert error["type"] == "extra_forbidden"
    assert error["input"] == {"values": ["nan", "inf", "-inf", 1.5]}
    assert cursor.query is None


def test_database_failure_is_not_hidden(label_type_client):
    client, cursor = label_type_client

    async def fail(*args, **kwargs):
        raise RuntimeError("Database unavailable")

    cursor.execute = fail
    with pytest.raises(RuntimeError, match="Database unavailable"):
        client.get("/api/v2/label_type/")
