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


def test_sheet_types_seed_and_restart_preserve_existing_resources(monkeypatch):
    schema = f"sheet_test_{uuid4().hex}"
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
                types = client.get("/api/v2/sheet_types/").json()
                assert types == [
                    {
                        "code": "456090",
                        "description": "A4 sheet with 60x90 mm labels",
                        "height": 60.0,
                        "width": 90.0,
                        "folded": False,
                    }
                ]
                assert client.get("/api/v2/sheet_types/456090").json() == types[0]
                assert client.get("/api/v2/sheet_types/456090/layouts").json() == ["layout_1"]
                nametag_types = client.get("/api/v2/nametag_types/").json()
                layouts = client.get("/api/v2/layouts/").json()
                event = client.post(
                    "/api/v2/events/",
                    json={
                        "name": "Preserved",
                        "start_date": "2026-10-19",
                        "end_date": "2026-10-20",
                    },
                ).json()
            connection.execute(
                sql.SQL("UPDATE {}.sheet_type SET description = 'Changed', height = 1 WHERE code = '456090'").format(
                    sql.Identifier(schema)
                )
            )
            with TestClient(main.app) as client:
                assert client.get("/api/v2/sheet_types/").json() == types
                assert client.get("/api/v2/sheet_types/456090/layouts").json() == ["layout_1"]
                assert client.get("/api/v2/nametag_types/").json() == nametag_types
                assert client.get("/api/v2/layouts/").json() == layouts
                assert client.get(f"/api/v2/events/{event['code']}").json() == event
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
