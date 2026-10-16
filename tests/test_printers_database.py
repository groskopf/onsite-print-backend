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

EXPECTED_PRINTERS = [
    {"code": "ALGCSOIO9O_A", "label": "A"},
    {"code": "1OPYKBGXVN_1", "label": "1"},
    {"code": "YF1OF5FWZ0_2", "label": "2"},
    {"code": "EK4M52IXL5_3", "label": "3"},
    {"code": "C0ZE5TM5V4_4", "label": "4"},
    {"code": "21YG0T6L8O_5", "label": "5"},
    {"code": "GRR4QKRYZP_6", "label": "6"},
    {"code": "R6CYBCY2XF_7", "label": "7"},
    {"code": "8SCNWZUF9M_8", "label": "8"},
    {"code": "AQ3PB4Q7I0_9", "label": "9"},
    {"code": "COIC1PEJ8J_10", "label": "10"},
    {"code": "S29DO41FE5_11", "label": "11"},
    {"code": "MXSI42S9W0_12", "label": "12"},
]
for printer in EXPECTED_PRINTERS:
    printer["description"] = (
        "Virtual printer used to generate labels" if printer["label"] == "A"
        else "1st generation black flight case"
    )


def test_seed_create_lookup_and_restart(monkeypatch):
    schema = f"printer_test_{uuid4().hex}"
    conninfo = os.environ["TEST_DATABASE_CONNINFO"]
    with connect(conninfo, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            connection.execute(sql.SQL("""
                CREATE TABLE {}.printer (
                    code TEXT PRIMARY KEY,
                    label TEXT NOT NULL
                )
            """).format(sql.Identifier(schema)))
            connection.execute(sql.SQL("""
                INSERT INTO {}.printer (code, label) VALUES ('ALGCSOIO9O_A', 'A')
            """).format(sql.Identifier(schema)))
            monkeypatch.setattr(
                main, "create_pool",
                lambda: AsyncConnectionPool(
                    conninfo, open=False, kwargs={"options": f"-c search_path={schema}"},
                ),
            )
            expected = sorted(EXPECTED_PRINTERS, key=lambda printer: printer["code"])
            with TestClient(main.app) as client:
                assert client.get("/api/v2/printer/").json() == expected
                for printer in expected:
                    response = client.get(f"/api/v2/printer/{printer['code']}")
                    assert response.status_code == 200
                    assert response.json() == printer
                new_printer = {
                    "code": "NEWPRINTER_13", "label": "13", "description": "New flight case",
                }
                payload = {"code": new_printer["code"], "description": new_printer["description"]}
                response = client.put("/api/v2/printer/", json=payload)
                assert response.status_code == 201
                assert response.json() == new_printer
                assert client.put(
                    "/api/v2/printer/", json=payload,
                ).status_code == 409
                assert client.get("/api/v2/printer/UNKNOWN_1").status_code == 404
                assert client.get("/api/v2/printer/").status_code == 200
            connection.execute(sql.SQL("""
                UPDATE {}.printer SET description = 'Updated virtual printer'
                WHERE code = 'ALGCSOIO9O_A'
            """).format(sql.Identifier(schema)))
            expected = [
                {**printer, "description": "Updated virtual printer"}
                if printer["code"] == "ALGCSOIO9O_A" else printer
                for printer in expected
            ]
            with TestClient(main.app) as client:
                response = client.get("/api/v2/printer/")
                assert response.status_code == 200
                assert response.json() == sorted(
                    expected + [new_printer], key=lambda printer: printer["code"],
                )
                paths = client.get("/api/v2/openapi.json").json()["paths"]
                assert {"get", "put"} <= paths["/api/v2/printer/"].keys()
                assert "get" in paths["/api/v2/printer/{code}"]
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
