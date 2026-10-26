from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import bookings
from app.bookings import router
from app.db import get_connection
from app.main import API_PREFIX

EVENT = {"code": "QRSTUVWXYZ234567"}
BOOKING_ROW = {
    "booking_code": "ABCDEFGHIJKLMNOP",
    "event_code": "QRSTUVWXYZ234567",
    "printer_code": "ALGCSOIO9O_A",
    "nametag_type": "4760100",
    "start_date": "2026-11-01",
    "end_date": "2026-11-03",
}
PAYLOAD = {key: value for key, value in BOOKING_ROW.items() if key != "booking_code"}


class FakeCursor:
    def __init__(self, results):
        self.results = list(results)
        self.queries = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, query, params=None):
        self.queries.append((query, params))

    async def fetchone(self):
        return self.results.pop(0)

    async def fetchall(self):
        return self.results.pop(0)


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def cursor(self, *, row_factory):
        return self.fake_cursor


def make_client(results):
    cursor = FakeCursor(results)

    async def fake_connection():
        yield FakeConnection(cursor)

    app = FastAPI()
    app.include_router(router, prefix=API_PREFIX)
    app.dependency_overrides[get_connection] = fake_connection
    return TestClient(app), cursor


def test_list_bookings():
    client, cursor = make_client([[BOOKING_ROW]])
    response = client.get(f"{API_PREFIX}/bookings/")
    assert response.status_code == 200
    assert response.json() == [BOOKING_ROW]
    assert "ORDER BY start_date" in cursor.queries[0][0]


def test_list_bookings_empty():
    client, _ = make_client([[]])
    assert client.get(f"{API_PREFIX}/bookings/").json() == []


def test_create_booking(monkeypatch):
    monkeypatch.setattr(bookings, "generate_code", lambda: BOOKING_ROW["booking_code"])
    client, cursor = make_client([EVENT, {"code": "ALGCSOIO9O_A"}, {"code": "4760100"}, None, BOOKING_ROW])
    response = client.post(f"{API_PREFIX}/bookings/", json=PAYLOAD)
    assert response.status_code == 201
    assert response.json() == BOOKING_ROW
    assert cursor.queries[0][1] == (EVENT["code"],)
    assert "FOR UPDATE" in cursor.queries[1][0]
    assert cursor.queries[3][1] == ("ALGCSOIO9O_A", date(2026, 11, 3), date(2026, 11, 1), None)
    assert cursor.queries[4][1][:2] == (BOOKING_ROW["booking_code"], EVENT["code"])


@pytest.mark.parametrize(
    ("results", "status_code", "detail"),
    [
        ([None], 404, "Event not found"),
        ([EVENT, None], 404, "Printer not found"),
        ([EVENT, {"code": "ALGCSOIO9O_A"}, None], 404, "Nametag type not found"),
        (
            [EVENT, {"code": "ALGCSOIO9O_A"}, {"code": "4760100"}, {"?column?": 1}],
            409,
            "Printer has booking in same period",
        ),
    ],
)
def test_create_booking_rejections(results, status_code, detail):
    client, cursor = make_client(results)
    response = client.post(f"{API_PREFIX}/bookings/", json=PAYLOAD)
    assert response.status_code == status_code
    assert response.json() == {"detail": detail}
    assert not any("INSERT" in query for query, _ in cursor.queries)


@pytest.mark.parametrize(
    "changes",
    [
        {"end_date": "2026-10-31"},
        {"printer_code": "invalid"},
        {"nametag_type": "abc"},
        {"start_date": "not-a-date"},
        {"booking_code": "ABCDEFGHIJKLMNOP"},
        {"event_code": "qrstuvwxyz234567"},
        {"event_code": "QRSTUVWXYZ23456"},
        {"event_code": None},
    ],
)
def test_create_booking_validation(changes):
    client, cursor = make_client([])
    response = client.post(f"{API_PREFIX}/bookings/", json={**PAYLOAD, **changes})
    assert response.status_code == 422
    assert cursor.queries == []


@pytest.mark.parametrize("missing", [{}, {key: value for key, value in PAYLOAD.items() if key != "event_code"}])
def test_create_booking_requires_fields(missing):
    client, _ = make_client([])
    assert client.post(f"{API_PREFIX}/bookings/", json=missing).status_code == 422


def test_generate_code():
    code = bookings.generate_code()
    assert len(code) == 16
    assert set(code) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")


ITEM_URL = f"{API_PREFIX}/bookings/{BOOKING_ROW['booking_code']}"


def test_get_booking():
    client, cursor = make_client([BOOKING_ROW])
    response = client.get(ITEM_URL)
    assert response.status_code == 200
    assert response.json() == BOOKING_ROW
    assert cursor.queries[0][1] == (BOOKING_ROW["booking_code"],)


@pytest.mark.parametrize("method", ["get", "delete"])
def test_missing_booking(method):
    client, _ = make_client([None])
    response = getattr(client, method)(ITEM_URL)
    assert response.status_code == 404
    assert response.json() == {"detail": "Booking not found"}


@pytest.mark.parametrize("method", ["get", "put", "delete"])
@pytest.mark.parametrize("code", ["abcdefghijklmnop", "ABCDEFGHIJKLMNO", "ABCDEFGHIJKLMNO1", "ABCDEFGHIJKLMNOPQ"])
def test_invalid_booking_code(method, code):
    client, cursor = make_client([])
    response = client.request(method, f"{API_PREFIX}/bookings/{code}", json=PAYLOAD)
    assert response.status_code == 422
    assert cursor.queries == []


def test_update_booking():
    updated = {**BOOKING_ROW, "end_date": "2026-11-04"}
    client, cursor = make_client([{"?column?": 1}, EVENT, {"code": "ALGCSOIO9O_A"}, {"code": "4760100"}, None, updated])
    response = client.put(ITEM_URL, json={**PAYLOAD, "end_date": "2026-11-04"})
    assert response.status_code == 200
    assert response.json() == updated
    assert "FOR UPDATE" in cursor.queries[0][0]
    assert cursor.queries[4][1][-1] == BOOKING_ROW["booking_code"]
    assert "UPDATE booking" in cursor.queries[5][0]
    assert cursor.queries[5][1][0] == EVENT["code"]


@pytest.mark.parametrize(
    ("results", "detail"),
    [
        ([None], "Booking not found"),
        ([{"?column?": 1}, None], "Event not found"),
        ([{"?column?": 1}, EVENT, None], "Printer not found"),
        ([{"?column?": 1}, EVENT, {"code": "ALGCSOIO9O_A"}, None], "Nametag type not found"),
    ],
)
def test_update_booking_not_found(results, detail):
    client, cursor = make_client(results)
    response = client.put(ITEM_URL, json=PAYLOAD)
    assert response.status_code == 404
    assert response.json() == {"detail": detail}
    assert not any("UPDATE booking" in query for query, _ in cursor.queries)


def test_update_booking_overlap():
    client, cursor = make_client(
        [{"?column?": 1}, EVENT, {"code": "ALGCSOIO9O_A"}, {"code": "4760100"}, {"?column?": 1}]
    )
    response = client.put(ITEM_URL, json=PAYLOAD)
    assert response.status_code == 409
    assert not any("UPDATE booking" in query for query, _ in cursor.queries)


@pytest.mark.parametrize("changes", [{"end_date": "2026-10-31"}, {"booking_code": "ABCDEFGHIJKLMNOP"}])
def test_update_booking_validation(changes):
    client, cursor = make_client([])
    assert client.put(ITEM_URL, json={**PAYLOAD, **changes}).status_code == 422
    assert client.put(ITEM_URL, json={}).status_code == 422
    assert cursor.queries == []


def test_delete_booking():
    client, cursor = make_client([BOOKING_ROW])
    response = client.delete(ITEM_URL)
    assert response.status_code == 200
    assert response.json() == BOOKING_ROW
    assert cursor.queries[0][0].startswith("DELETE FROM booking")
