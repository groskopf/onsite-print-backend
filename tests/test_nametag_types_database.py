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

EXPECTED = [
    {"code": "4760100", "description": "Name tag 60x100 mm", "height": 60.0, "width": 100.0, "folded": False},
    {"code": "4786103", "description": "Foldable name tag 86x103 mm", "height": 86.0, "width": 103.0, "folded": True},
]


def test_nametag_types_are_static_and_read_only(monkeypatch):
    schema = f"nametag_type_test_{uuid4().hex}"
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
                assert client.get("/api/v2/nametag_types/").json() == EXPECTED
                assert client.get("/api/v2/nametag_types/4760100").json() == EXPECTED[0]
                assert client.get("/api/v2/nametag_types/999").status_code == 404
                assert client.post("/api/v2/nametag_types/", json=EXPECTED[0]).status_code == 405
                assert client.put("/api/v2/nametag_types/4760100", json=EXPECTED[0]).status_code == 405
                assert client.delete("/api/v2/nametag_types/4760100").status_code == 405
                paths = client.get("/api/v2/openapi.json").json()["paths"]
                assert set(paths["/api/v2/nametag_types/"]) == {"get"}
                assert set(paths["/api/v2/nametag_types/{code}"]) == {"get"}

            connection.execute(sql.SQL(
                "UPDATE {}.nametag_type SET description = 'Changed', height = 1 WHERE code = '4760100'"
            ).format(sql.Identifier(schema)))
            with TestClient(main.app) as client:
                assert client.get("/api/v2/nametag_types/").json() == EXPECTED
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
