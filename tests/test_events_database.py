import os

import pytest
from fastapi.testclient import TestClient
from psycopg_pool import AsyncConnectionPool

from app import main

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_CONNINFO"),
    reason="Set TEST_DATABASE_CONNINFO to an isolated PostgreSQL test database",
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
            assert event == {**payload, "id": event["id"]}
            created.append(event)
        assert created[0]["id"] < created[1]["id"]
        assert client.get("/api/v2/event/").json() == previous + created

    with TestClient(main.app) as client:
        assert client.get("/api/v2/event/").json() == previous + created
        schema = client.get("/api/v2/openapi.json").json()
        assert {"get", "put"} <= schema["paths"]["/api/v2/event/"].keys()
