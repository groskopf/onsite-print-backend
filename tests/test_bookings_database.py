import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg import connect, sql
from psycopg_pool import AsyncConnectionPool

from app import main

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_CONNINFO"),
    reason="Set TEST_DATABASE_CONNINFO to an isolated PostgreSQL test database",
)

URL = "/api/v2/bookings/"
EVENT = {"name": "Print conference", "start_date": "2026-11-01", "end_date": "2026-11-12"}
event_codes: list[str] = []


def booking(printer_code, start_date, end_date, nametag_type="4760100", event_code=None):
    return {
        "event_code": event_code or event_codes[0],
        "printer_code": printer_code,
        "nametag_type": nametag_type,
        "start_date": start_date,
        "end_date": end_date,
    }


def test_create_list_overlap_and_restart(monkeypatch):
    schema = f"booking_test_{uuid4().hex}"
    conninfo = os.environ["TEST_DATABASE_CONNINFO"]
    with connect(conninfo, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            monkeypatch.setattr(
                main,
                "create_pool",
                lambda: AsyncConnectionPool(
                    conninfo,
                    open=False,
                    kwargs={"options": f"-c search_path={schema}"},
                ),
            )
            with TestClient(main.app) as client:
                assert client.get(URL).json() == []
                event_codes[:] = [client.post("/api/v2/events/", json=EVENT).json()["code"] for _ in range(2)]
                missing_event = booking("1OPYKBGXVN_1", "2027-02-01", "2027-02-01", event_code="AAAAAAAAAAAAAAAA")
                response = client.post(URL, json=missing_event)
                assert response.status_code == 404
                assert response.json() == {"detail": "Event not found"}

                second = client.post(URL, json=booking("1OPYKBGXVN_1", "2026-11-05", "2026-11-06"))
                first = client.post(URL, json=booking("1OPYKBGXVN_1", "2026-11-01", "2026-11-03", "4786103"))
                other_printer = client.post(URL, json=booking("YF1OF5FWZ0_2", "2026-11-01", "2026-11-03"))
                same_day = client.post(URL, json=booking("1OPYKBGXVN_1", "2026-11-04", "2026-11-04"))
                for response in (first, second, other_printer, same_day):
                    assert response.status_code == 201
                    assert len(response.json()["booking_code"]) == 16
                assert first.json() == {
                    **booking("1OPYKBGXVN_1", "2026-11-01", "2026-11-03", "4786103"),
                    "booking_code": first.json()["booking_code"],
                }

                for start_date, end_date in (
                    ("2026-11-03", "2026-11-03"),
                    ("2026-10-25", "2026-11-01"),
                    ("2026-11-06", "2026-11-10"),
                    ("2026-10-01", "2026-12-01"),
                ):
                    response = client.post(URL, json=booking("1OPYKBGXVN_1", start_date, end_date))
                    assert response.status_code == 409
                    assert response.json() == {"detail": "Printer has booking in same period"}

                assert client.post(URL, json=booking("UNKNOWN_1", "2026-11-01", "2026-11-01")).status_code == 404
                assert (
                    client.post(URL, json=booking("1OPYKBGXVN_1", "2027-01-01", "2027-01-01", "999")).status_code == 404
                )
                assert client.post(URL, json=booking("1OPYKBGXVN_1", "2027-01-02", "2027-01-01")).status_code == 422

                expected = [first.json(), other_printer.json(), same_day.json(), second.json()]
                assert client.get(URL).json() == expected

            with TestClient(main.app) as client:
                assert client.get(URL).json() == expected
                paths = client.get("/api/v2/openapi.json").json()["paths"]
                assert {"get", "post"} <= paths[URL].keys()
                assert {"get", "put", "delete"} <= paths[URL + "{booking_code}"].keys()

                first_url = URL + first.json()["booking_code"]
                assert client.get(first_url).json() == first.json()

                # Updating a booking may overlap its own previous period.
                moved = booking("1OPYKBGXVN_1", "2026-11-02", "2026-11-03", "4760100")
                response = client.put(first_url, json=moved)
                assert response.status_code == 200
                assert response.json() == {**moved, "booking_code": first.json()["booking_code"]}
                assert client.get(first_url).json() == response.json()

                response = client.put(first_url, json=booking("1OPYKBGXVN_1", "2026-11-02", "2026-11-04"))
                assert response.status_code == 409
                assert client.put(first_url, json=booking("UNKNOWN_1", "2026-11-02", "2026-11-03")).status_code == 404
                assert client.put(URL + "AAAAAAAAAAAAAAAA", json=moved).status_code == 404

                response = client.put(
                    first_url, json=booking("YF1OF5FWZ0_2", "2026-11-10", "2026-11-12", event_code=event_codes[1])
                )
                assert response.status_code == 200
                assert response.json()["printer_code"] == "YF1OF5FWZ0_2"
                assert response.json()["event_code"] == event_codes[1]
                assert client.put(first_url, json={**moved, "event_code": "AAAAAAAAAAAAAAAA"}).status_code == 404

                response = client.delete(first_url)
                assert response.status_code == 200
                assert response.json()["booking_code"] == first.json()["booking_code"]
                assert client.get(first_url).status_code == 404
                assert client.delete(first_url).status_code == 404
                assert client.post(URL, json=booking("1OPYKBGXVN_1", "2026-11-01", "2026-11-03")).status_code == 201
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.parametrize("has_booking", [False, True])
def test_existing_booking_table_gains_required_event_code(monkeypatch, has_booking):
    schema = f"booking_upgrade_test_{uuid4().hex}"
    conninfo = os.environ["TEST_DATABASE_CONNINFO"]
    with connect(conninfo, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            monkeypatch.setattr(
                main,
                "create_pool",
                lambda: AsyncConnectionPool(conninfo, open=False, kwargs={"options": f"-c search_path={schema}"}),
            )
            with TestClient(main.app):
                pass
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            connection.execute("ALTER TABLE booking DROP COLUMN event_code")
            if has_booking:
                connection.execute("""
                    INSERT INTO booking (booking_code, printer_code, nametag_type, start_date, end_date)
                    VALUES ('AAAAAAAAAAAAAAAA', '1OPYKBGXVN_1', '4760100', '2026-11-01', '2026-11-01')
                """)
                with pytest.raises(Exception, match="Bookings without event_code exist"):
                    with TestClient(main.app):
                        pass
            else:
                with TestClient(main.app) as client:
                    assert client.get(URL).json() == []
                    event_code = client.post("/api/v2/events/", json=EVENT).json()["code"]
                    payload = booking("1OPYKBGXVN_1", "2026-11-01", "2026-11-01", event_code=event_code)
                    response = client.post(URL, json=payload)
                    assert response.status_code == 201
                    assert response.json()["event_code"] == event_code
                (nullable,) = connection.execute("""
                    SELECT is_nullable FROM information_schema.columns
                    WHERE table_schema = current_schema() AND table_name = 'booking' AND column_name = 'event_code'
                """).fetchone()
                assert nullable == "NO"
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
