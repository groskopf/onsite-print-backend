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
            for _ in range(2):
                with TestClient(main.app) as client:
                    response = client.get("/api/v2/event/")
                    assert response.status_code == 200
                    assert response.json() == [{
                        "id": 1,
                        "name": "Existing event",
                        "start_date": "2026-10-09",
                        "end_date": "2026-10-10",
                        "street": None, "zip": None, "town": None, "country": None,
                    }]
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
        previous = client.get("/api/v2/event/").json()
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
            response = client.put("/api/v2/event/", json=payload)
            assert response.status_code == 201
            event = response.json()
            assert event == {
                "street": None, "zip": None, "town": None, "country": None,
                **payload, "id": event["id"],
            }
            created.append(event)
        assert created[0]["id"] < created[1]["id"]
        assert client.get("/api/v2/event/").json() == previous + created

    with TestClient(main.app) as client:
        assert client.get("/api/v2/event/").json() == previous + created
        schema = client.get("/api/v2/openapi.json").json()
        assert {"get", "put"} <= schema["paths"]["/api/v2/event/"].keys()
