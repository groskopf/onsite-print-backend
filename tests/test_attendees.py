import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import codes, endpoint_attendees
from app.db import get_connection
from app.endpoint_attendees import router
from app.main import API_PREFIX

EVENT_CODE = "ABCDEFGHIJKLMNOP"
CODE = "a" * 64
URL = f"{API_PREFIX}/attendees/{EVENT_CODE}"
PAYLOAD = {"first_name": "Ada", "last_name": "Lovelace"}
OPTIONAL = dict.fromkeys(("address", "zip", "city", "country", "company", "phone", "email"))
DETAILS = {
    "address": "Main Street 12",
    "zip": "0123",
    "city": "Copenhagen",
    "country": "Denmark",
    "company": "Print company",
    "phone": "+45 01234567",
    "email": "ada@example.com",
}


class FakeCursor:
    def __init__(self):
        self.rows = [{**PAYLOAD, **OPTIONAL, "event_code": EVENT_CODE, "code": CODE}]
        self.event_exists = True
        self.duplicate = None
        self.queries = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, query, params=None):
        self.queries.append((query, params))

    async def fetchone(self):
        if self.queries[-1][0].startswith("SELECT 1 FROM event"):
            return (1,) if self.event_exists else None
        if "lower(first_name)" in self.queries[-1][0]:
            return self.duplicate
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def cursor(self, **kwargs):
        return self.fake_cursor


@pytest.fixture
def attendee_client():
    cursor = FakeCursor()

    async def fake_connection():
        yield FakeConnection(cursor)

    app = FastAPI()
    app.include_router(router, prefix=API_PREFIX)
    app.dependency_overrides[get_connection] = fake_connection
    with TestClient(app) as client:
        yield client, cursor


def test_create_attendee(attendee_client, monkeypatch):
    client, cursor = attendee_client
    monkeypatch.setattr(endpoint_attendees, "generate_attendee_code", lambda: CODE)
    cursor.rows[0].update(DETAILS)
    payload = {key: f"  {value}  " for key, value in {**PAYLOAD, **DETAILS}.items()}
    response = client.post(URL, json=payload)
    assert response.status_code == 201
    assert response.json() == {**PAYLOAD, **DETAILS, "code": CODE, "event_code": EVENT_CODE}
    query, params = cursor.queries[-1]
    assert "INSERT INTO attendee" in query
    assert params == (CODE, EVENT_CODE, *PAYLOAD.values(), *DETAILS.values())
    assert "FOR KEY SHARE" in cursor.queries[0][0]


def test_duplicate_attendee_is_rejected_with_code(attendee_client):
    client, cursor = attendee_client
    existing = {**PAYLOAD, **DETAILS, "event_code": EVENT_CODE, "code": CODE}
    cursor.duplicate = existing
    response = client.post(URL, json={"first_name": " ada ", "last_name": "LOVELACE", "email": "Ada@Example.com"})
    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "attendee_exists",
            "message": "An attendee with the same name and email already exists",
            "attendee": existing,
        }
    }
    lock, check = cursor.queries[1:]
    assert "pg_advisory_xact_lock" in lock[0]
    assert lock[1] == (f"attendee:{EVENT_CODE}",)
    assert "lower(email) IS NOT DISTINCT FROM lower(%s)" in check[0]
    assert check[1] == (EVENT_CODE, "ada", "LOVELACE", "Ada@Example.com")
    assert all("INSERT" not in query for query, _ in cursor.queries)


@pytest.mark.parametrize("value", ["true", "1"])
def test_allow_duplicate_skips_duplicate_check(attendee_client, value):
    client, cursor = attendee_client
    cursor.duplicate = cursor.rows[0]
    response = client.post(URL, params={"allow_duplicate": value}, json=PAYLOAD)
    assert response.status_code == 201
    assert "pg_advisory_xact_lock" in cursor.queries[1][0]
    assert "INSERT INTO attendee" in cursor.queries[2][0]
    assert all("lower(first_name)" not in query for query, _ in cursor.queries)


def test_unique_attendee_is_checked_before_insert(attendee_client):
    client, cursor = attendee_client
    assert client.post(URL, params={"allow_duplicate": "false"}, json=PAYLOAD).status_code == 201
    assert "lower(first_name)" in cursor.queries[2][0]
    assert cursor.queries[2][1] == (EVENT_CODE, "Ada", "Lovelace", None)
    assert "INSERT INTO attendee" in cursor.queries[3][0]


def test_invalid_allow_duplicate_is_rejected(attendee_client):
    client, cursor = attendee_client
    assert client.post(URL, params={"allow_duplicate": "maybe"}, json=PAYLOAD).status_code == 422
    assert all("attendee" not in query for query, _ in cursor.queries)


def test_optional_details_default_to_null(attendee_client):
    client, cursor = attendee_client
    for payload in (PAYLOAD, {**PAYLOAD, **OPTIONAL}):
        response = client.post(URL, json=payload)
        assert response.status_code == 201
        assert response.json() == {**PAYLOAD, **OPTIONAL, "code": CODE, "event_code": EVENT_CODE}
        assert cursor.queries[-1][1][4:] == (None,) * len(OPTIONAL)


def test_list_attendees(attendee_client):
    client, cursor = attendee_client
    response = client.get(URL)
    assert response.status_code == 200
    assert response.json() == cursor.rows
    query, params = cursor.queries[-1]
    assert "WHERE event_code = %s ORDER BY last_name, first_name, code" in query
    assert params == (EVENT_CODE,)
    cursor.rows = []
    assert client.get(URL).json() == []


@pytest.mark.parametrize("method", ["get", "delete"])
def test_read_and_delete_are_event_scoped(attendee_client, method):
    client, cursor = attendee_client
    response = getattr(client, method)(f"{URL}/{CODE}")
    assert response.status_code == 200
    assert response.json() == cursor.rows[0]
    query, params = cursor.queries[-1]
    assert "WHERE event_code = %s AND code = %s" in query
    assert params == (EVENT_CODE, CODE)


def test_replace_attendee_preserves_code_and_event(attendee_client):
    client, cursor = attendee_client
    cursor.rows[0].update(first_name="Grace", last_name="Hopper")
    response = client.put(f"{URL}/{CODE}", json={"first_name": "  Grace  ", "last_name": "Hopper"})
    assert response.status_code == 200
    assert response.json() == {**cursor.rows[0], **OPTIONAL}
    query, params = cursor.queries[-1]
    assert "UPDATE attendee" in query
    assert "code =" not in query.split("WHERE")[0]
    assert params == ("Grace", "Hopper", *((None,) * len(OPTIONAL)), EVENT_CODE, CODE)


@pytest.mark.parametrize("field", [*PAYLOAD, *OPTIONAL])
@pytest.mark.parametrize("value", ["", "  ", 123, [], {}])
@pytest.mark.parametrize("method", ["post", "put"])
def test_invalid_fields_are_rejected(attendee_client, field, value, method):
    client, cursor = attendee_client
    url = URL if method == "post" else f"{URL}/{CODE}"
    assert getattr(client, method)(url, json={**PAYLOAD, field: value}).status_code == 422
    assert all("attendee" not in query for query, _ in cursor.queries)


@pytest.mark.parametrize("field", PAYLOAD)
@pytest.mark.parametrize("value", [None, "missing"])
def test_names_are_required(attendee_client, field, value):
    client, _ = attendee_client
    payload = {**PAYLOAD, field: value}
    if value == "missing":
        del payload[field]
    assert client.post(URL, json=payload).status_code == 422


@pytest.mark.parametrize("field", ["code", "event_code", "id", "unknown"])
@pytest.mark.parametrize("method", ["post", "put"])
def test_server_fields_and_unknown_fields_are_rejected(attendee_client, field, method):
    client, _ = attendee_client
    url = URL if method == "post" else f"{URL}/{CODE}"
    assert getattr(client, method)(url, json={**PAYLOAD, field: CODE}).status_code == 422


@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_missing_attendee(attendee_client, method):
    client, cursor = attendee_client
    cursor.rows = []
    kwargs = {"json": PAYLOAD} if method == "put" else {}
    response = getattr(client, method)(f"{URL}/{CODE}", **kwargs)
    assert response.status_code == 404
    assert response.json() == {"detail": "Attendee not found"}


@pytest.mark.parametrize(
    ("method", "item"),
    [("post", False), ("get", False), ("get", True), ("put", True), ("delete", True)],
)
def test_missing_event(attendee_client, method, item):
    client, cursor = attendee_client
    cursor.event_exists = False
    kwargs = {"json": PAYLOAD} if method in ("post", "put") else {}
    response = getattr(client, method)(f"{URL}/{CODE}" if item else URL, **kwargs)
    assert response.status_code == 404
    assert response.json() == {"detail": "Event not found"}
    assert len(cursor.queries) == 1


@pytest.mark.parametrize("code", ["a" * 63, "a" * 65, "A" * 64, "g" * 64, "short"])
@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_invalid_attendee_codes(attendee_client, code, method):
    client, cursor = attendee_client
    kwargs = {"json": PAYLOAD} if method == "put" else {}
    assert getattr(client, method)(f"{URL}/{code}", **kwargs).status_code == 422
    assert all("attendee" not in query for query, _ in cursor.queries)


def test_invalid_event_code(attendee_client):
    client, cursor = attendee_client
    assert client.get(f"{API_PREFIX}/attendees/invalid").status_code == 422
    assert cursor.queries == []


def test_database_errors_are_not_hidden(attendee_client):
    client, cursor = attendee_client

    async def fail(*args, **kwargs):
        raise RuntimeError("Database unavailable")

    cursor.execute = fail
    with pytest.raises(RuntimeError, match="Database unavailable"):
        client.get(URL)


def test_codes_are_unique_256_bit_hex_strings():
    generated = {codes.generate_attendee_code() for _ in range(100)}
    assert len(generated) == 100
    assert all(re.fullmatch(codes.ATTENDEE_CODE_PATTERN, code) for code in generated)


def test_code_generation_uses_32_secure_random_bytes(monkeypatch):
    calls = []

    def token_hex(size):
        calls.append(size)
        return CODE

    monkeypatch.setattr(codes.secrets, "token_hex", token_hex)
    assert codes.generate_attendee_code() == CODE
    assert calls == [32]
