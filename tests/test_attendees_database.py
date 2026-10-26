import os
import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg import connect, errors, sql
from psycopg_pool import AsyncConnectionPool

from app import main
from app.codes import ATTENDEE_CODE_PATTERN

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_CONNINFO"),
    reason="Set TEST_DATABASE_CONNINFO to an isolated PostgreSQL test database",
)


def test_attendee_lifecycle_persistence_and_event_isolation(monkeypatch):
    schema = f"attendee_test_{uuid4().hex}"
    conninfo = os.environ["TEST_DATABASE_CONNINFO"]
    with connect(conninfo, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            monkeypatch.setattr(
                main,
                "create_pool",
                lambda: AsyncConnectionPool(conninfo, open=False, kwargs={"options": f"-c search_path={schema}"}),
            )
            with TestClient(main.app) as client:
                event_payload = {"name": "Conference", "start_date": "2026-11-01", "end_date": "2026-11-02"}
                events = [client.post("/api/v2/events/", json=event_payload).json() for _ in range(2)]
                urls = [f"/api/v2/attendees/{event['code']}" for event in events]
                assert client.get(urls[0]).json() == []
                payload = {
                    "first_name": "Ada",
                    "last_name": "O'Brien'); DROP TABLE attendee; --",
                    "address": "Main Street 12",
                    "zip": "0123",
                    "city": "Copenhagen",
                    "country": "Denmark",
                    "company": "Print",
                    "phone": "+45 01234567",
                    "email": "ada@example.com",
                }
                created = []
                for url in urls:
                    response = client.post(url, json=payload)
                    assert response.status_code == 201
                    created.append(response.json())
                first, second = created
                assert first["code"] != second["code"]
                assert all(re.fullmatch(ATTENDEE_CODE_PATTERN, row["code"]) for row in created)
                assert first == {**payload, "code": first["code"], "event_code": events[0]["code"]}
                item_url = f"{urls[0]}/{first['code']}"
                assert client.get(item_url).json() == first
                assert client.get(urls[0]).json() == [first]
                assert client.get(urls[1]).json() == [second]
                for method in ("get", "put", "delete"):
                    kwargs = {"json": payload} if method == "put" else {}
                    assert getattr(client, method)(f"{urls[1]}/{first['code']}", **kwargs).status_code == 404
                update = {"first_name": "Grace", "last_name": "Hopper"}
                response = client.put(item_url, json=update)
                assert response.status_code == 200
                updated = response.json()
                assert updated == {
                    **dict.fromkeys(("address", "zip", "city", "country", "company", "phone", "email")),
                    **update,
                    "code": first["code"],
                    "event_code": events[0]["code"],
                }
                paths = client.get("/api/v2/openapi.json").json()["paths"]
                assert {"get", "post"} <= paths["/api/v2/attendees/{event_code}"].keys()
                assert {"get", "put", "delete"} <= paths["/api/v2/attendees/{event_code}/{attendee_code}"].keys()

            with TestClient(main.app) as client:
                assert client.get(item_url).json() == updated
                assert client.get(urls[1]).json() == [second]
                response = client.delete(item_url)
                assert response.status_code == 200
                assert response.json() == updated
                assert client.get(item_url).status_code == 404
                assert client.get(urls[0]).json() == []
                replacement = client.post(urls[0], json=payload).json()
                assert client.delete(f"/api/v2/events/{events[0]['code']}").status_code == 200
                assert client.get(urls[0]).status_code == 404
                assert client.get(urls[1]).json() == [second]
                rows = connection.execute(
                    sql.SQL("SELECT code FROM {}.attendee ORDER BY code").format(sql.Identifier(schema))
                ).fetchall()
                assert rows == [(second["code"],)]
                assert replacement["code"] != first["code"]

                table = sql.Identifier(schema, "attendee")
                for field, value, error in (
                    ("first_name", "  ", errors.CheckViolation),
                    ("last_name", None, errors.NotNullViolation),
                    ("zip", "", errors.CheckViolation),
                    ("code", "invalid", errors.CheckViolation),
                    ("event_code", "Z" * 16, errors.ForeignKeyViolation),
                ):
                    with pytest.raises(error):
                        connection.execute(
                            sql.SQL("UPDATE {} SET {} = %s WHERE code = %s").format(table, sql.Identifier(field)),
                            (value, second["code"]),
                        )
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_duplicate_attendees_require_permission(monkeypatch):
    schema = f"attendee_test_{uuid4().hex}"
    conninfo = os.environ["TEST_DATABASE_CONNINFO"]
    with connect(conninfo, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            monkeypatch.setattr(
                main,
                "create_pool",
                lambda: AsyncConnectionPool(conninfo, open=False, kwargs={"options": f"-c search_path={schema}"}),
            )
            with TestClient(main.app) as client:
                event_payload = {"name": "Conference", "start_date": "2026-11-01", "end_date": "2026-11-02"}
                events = [client.post("/api/v2/events/", json=event_payload).json() for _ in range(2)]
                url, other_url = (f"/api/v2/attendees/{event['code']}" for event in events)
                payload = {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com"}
                first = client.post(url, json=payload).json()

                duplicate = {"first_name": "ADA", "last_name": " lovelace ", "email": "Ada@Example.COM"}
                response = client.post(url, json=duplicate)
                assert response.status_code == 409
                assert response.json()["detail"]["code"] == "attendee_exists"
                assert response.json()["detail"]["attendee"] == first

                for different in ({**payload, "email": None}, {**payload, "email": "other@example.com"}):
                    assert client.post(url, json=different).status_code == 201
                assert client.post(url, json={**payload, "email": None}).status_code == 409
                assert client.post(other_url, json=payload).status_code == 201

                response = client.post(url, params={"allow_duplicate": "true"}, json=duplicate)
                assert response.status_code == 201
                assert response.json()["code"] != first["code"]
                assert len(client.get(url).json()) == 4
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
