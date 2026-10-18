import os
import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg import connect, sql
from psycopg_pool import AsyncConnectionPool

from app import main
from app.codes import CODE_PATTERN

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_CONNINFO"),
    reason="Set TEST_DATABASE_CONNINFO to an isolated PostgreSQL test database",
)


def test_existing_events_gain_nullable_addresses(monkeypatch):
    schema = f"event_address_test_{uuid4().hex}"
    conninfo = os.environ["TEST_DATABASE_CONNINFO"]
    with connect(conninfo, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            connection.execute(sql.SQL("""
                CREATE TABLE {}.event (
                    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    name TEXT NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL
                )
            """).format(sql.Identifier(schema)))
            connection.execute(sql.SQL("""
                INSERT INTO {}.event (name, start_date, end_date)
                VALUES ('Existing event', '2026-10-09', '2026-10-10')
            """).format(sql.Identifier(schema)))
            monkeypatch.setattr(
                main,
                "create_pool",
                lambda: AsyncConnectionPool(
                    conninfo, open=False, kwargs={"options": f"-c search_path={schema}"},
                ),
            )
            codes: list[str] = []
            for _ in range(2):
                with TestClient(main.app) as client:
                    response = client.get("/api/v2/events/")
                    assert response.status_code == 200
                    code = response.json()[0]["code"]
                    codes.append(code)
                    assert re.fullmatch(CODE_PATTERN, code)
                    assert response.json() == [{
                        "id": 1,
                        "code": code,
                        "name": "Existing event",
                        "start_date": "2026-10-09",
                        "end_date": "2026-10-10",
                        "street": None, "zip": None, "town": None, "country": None,
                    }]
            assert codes[0] == codes[1]
        finally:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def test_events_persist_across_app_restarts(monkeypatch):
    monkeypatch.setattr(
        main,
        "create_pool",
        lambda: AsyncConnectionPool(os.environ["TEST_DATABASE_CONNINFO"], open=False),
    )
    with TestClient(main.app) as client:
        previous = client.get("/api/v2/events/").json()
        payloads = [
            {
                "name": "Test event",
                "start_date": "2026-10-09",
                "end_date": "2026-10-10",
                "street": "Main Street 12",
                "zip": "0123",
                "town": "Copenhagen",
                "country": "Denmark",
            },
            {
                "name": "O'Brien's event'); DROP TABLE event; --",
                "start_date": "2026-11-01",
                "end_date": "2026-11-01",
            },
        ]
        created = []
        for payload in payloads:
            response = client.post("/api/v2/events/", json=payload)
            assert response.status_code == 201
            event = response.json()
            assert event == {
                "street": None, "zip": None, "town": None, "country": None,
                **payload, "id": event["id"], "code": event["code"],
            }
            created.append(event)
        assert created[0]["id"] < created[1]["id"]
        assert created[0]["code"] != created[1]["code"]
        assert all(re.fullmatch(CODE_PATTERN, event["code"]) for event in created)
        assert client.get("/api/v2/events/").json() == previous + created

    with TestClient(main.app) as client:
        assert client.get("/api/v2/events/").json() == previous + created
        schema = client.get("/api/v2/openapi.json").json()
        assert {"get", "post"} <= schema["paths"]["/api/v2/events/"].keys()


def test_get_update_and_delete_event_with_bookings(monkeypatch):
    schema = f"event_item_test_{uuid4().hex}"
    conninfo = os.environ["TEST_DATABASE_CONNINFO"]
    with connect(conninfo, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            monkeypatch.setattr(
                main,
                "create_pool",
                lambda: AsyncConnectionPool(
                    conninfo, open=False, kwargs={"options": f"-c search_path={schema}"},
                ),
            )
            payload = {"name": "Print conference", "start_date": "2026-11-01", "end_date": "2026-11-12"}
            with TestClient(main.app) as client:
                kept, removed = (client.post("/api/v2/events/", json=payload).json() for _ in range(2))
                url = f"/api/v2/events/{removed['code']}"
                assert client.get(url).json() == removed

                update = {**payload, "name": "Renamed", "town": "Aarhus"}
                response = client.put(url, json=update)
                assert response.status_code == 200
                updated = {**removed, **update}
                assert response.json() == updated
                assert client.get(url).json() == updated

                def booking(event, printer_code):
                    return client.post("/api/v2/bookings/", json={
                        "event_code": event["code"], "printer_code": printer_code,
                        "nametag_type": "4760100", "start_date": "2026-11-01", "end_date": "2026-11-02",
                    }).json()

                kept_booking = booking(kept, "1OPYKBGXVN_1")
                booking(removed, "YF1OF5FWZ0_2")
                booking(removed, "ALGCSOIO9O_A")

                response = client.delete(url)
                assert response.status_code == 200
                assert response.json() == updated
                assert client.get(url).status_code == 404
                assert client.put(url, json=update).status_code == 404
                assert client.delete(url).status_code == 404
                assert client.get("/api/v2/events/").json() == [kept]
                assert client.get("/api/v2/bookings/").json() == [kept_booking]
                paths = client.get("/api/v2/openapi.json").json()["paths"]
                assert {"get", "put", "delete"} <= paths["/api/v2/events/{event_code}"].keys()
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
