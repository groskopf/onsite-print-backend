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
        "Virtual printer used to generate labels" if printer["label"] == "A" else "1st generation black flight case"
    )


def use_schema(monkeypatch, conninfo, schema):
    monkeypatch.setattr(
        main,
        "create_pool",
        lambda: AsyncConnectionPool(conninfo, open=False, kwargs={"options": f"-c search_path={schema}"}),
    )


def by_code(printers):
    return sorted(printers, key=lambda printer: printer["code"])


def test_seed_crud_and_restart(monkeypatch):
    schema = f"printer_test_{uuid4().hex}"
    conninfo = os.environ["TEST_DATABASE_CONNINFO"]
    with connect(conninfo, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            use_schema(monkeypatch, conninfo, schema)
            expected = by_code(EXPECTED_PRINTERS)
            with TestClient(main.app) as client:
                assert client.get("/api/v2/printers/").json() == expected
                for printer in expected:
                    response = client.get(f"/api/v2/printers/{printer['code']}")
                    assert response.status_code == 200
                    assert response.json() == printer
                new_printer = {"code": "NEWPRINTER_13", "label": "13", "description": "New flight case"}
                payload = {"code": new_printer["code"], "description": new_printer["description"]}
                response = client.post("/api/v2/printers/", json=payload)
                assert response.status_code == 201
                assert response.json() == new_printer
                assert client.post("/api/v2/printers/", json=payload).status_code == 409
                assert client.get("/api/v2/printers/UNKNOWN_1").status_code == 404

                updated = {**expected[0], "description": "Updated description"}
                response = client.put(
                    f"/api/v2/printers/{updated['code']}",
                    json={"description": "Updated description"},
                )
                assert response.status_code == 200
                assert response.json() == updated
                assert client.put("/api/v2/printers/UNKNOWN_1", json={"description": "x"}).status_code == 404

                event_code = client.post(
                    "/api/v2/events/",
                    json={
                        "name": "Print conference",
                        "start_date": "2026-11-01",
                        "end_date": "2026-11-02",
                    },
                ).json()["code"]
                booked = "1OPYKBGXVN_1"
                assert (
                    client.post(
                        "/api/v2/bookings/",
                        json={
                            "event_code": event_code,
                            "printer_code": booked,
                            "nametag_type": "4760100",
                            "start_date": "2026-11-01",
                            "end_date": "2026-11-02",
                        },
                    ).status_code
                    == 201
                )
                response = client.delete(f"/api/v2/printers/{booked}")
                assert response.status_code == 409
                assert response.json() == {"detail": "Printer has bookings"}

                removed = "ALGCSOIO9O_A"
                response = client.delete(f"/api/v2/printers/{removed}")
                assert response.status_code == 200
                assert response.json()["code"] == removed
                assert client.get(f"/api/v2/printers/{removed}").status_code == 404
                assert client.delete(f"/api/v2/printers/{removed}").status_code == 404

            remaining = by_code(
                [updated if p["code"] == updated["code"] else p for p in expected if p["code"] != removed]
                + [new_printer]
            )
            with TestClient(main.app) as client:
                assert client.get("/api/v2/printers/").json() == remaining
                paths = client.get("/api/v2/openapi.json").json()["paths"]
                assert {"get", "post"} <= paths["/api/v2/printers/"].keys()
                assert {"get", "put", "delete"} <= paths["/api/v2/printers/{code}"].keys()

            connection.execute(sql.SQL("DELETE FROM {}.booking").format(sql.Identifier(schema)))
            connection.execute(sql.SQL("DELETE FROM {}.printer").format(sql.Identifier(schema)))
            with TestClient(main.app) as client:
                assert client.get("/api/v2/printers/").json() == expected
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_existing_printers_are_not_reseeded(monkeypatch):
    schema = f"printer_upgrade_test_{uuid4().hex}"
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
                INSERT INTO {}.printer (code, label) VALUES ('ALGCSOIO9O_A', 'A'), ('CUSTOM_X', 'X')
            """).format(sql.Identifier(schema)))
            use_schema(monkeypatch, conninfo, schema)
            for _ in range(2):
                with TestClient(main.app) as client:
                    assert client.get("/api/v2/printers/").json() == [
                        {
                            "code": "ALGCSOIO9O_A",
                            "label": "A",
                            "description": "Virtual printer used to generate labels",
                        },
                        {"code": "CUSTOM_X", "label": "X", "description": None},
                    ]
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
