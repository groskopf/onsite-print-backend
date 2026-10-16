import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg import connect, errors, sql
from psycopg_pool import AsyncConnectionPool

from app import main

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_CONNINFO"),
    reason="Set TEST_DATABASE_CONNINFO to an isolated PostgreSQL test database",
)

EXPECTED_LABEL_TYPES = [
    {
        "code": "4760100", "description": "Name tag 60x100 mm",
        "height": 60, "width": 100, "mirrored": False,
    },
    {
        "code": "4786103", "description": "Foldable name tag 86x103 mm",
        "height": 86, "width": 103, "mirrored": True,
    },
]


def test_seed_create_lookup_constraints_and_restart(monkeypatch):
    schema = f"label_type_test_{uuid4().hex}"
    conninfo = os.environ["TEST_DATABASE_CONNINFO"]
    with connect(conninfo, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            monkeypatch.setattr(
                main, "create_pool",
                lambda: AsyncConnectionPool(
                    conninfo, open=False, kwargs={"options": f"-c search_path={schema}"},
                ),
            )
            new_type = {
                "code": "0012345", "description": "Custom label",
                "height": 60.25, "width": 100.5, "mirrored": False,
            }
            with TestClient(main.app) as client:
                response = client.get("/api/v2/label_type/")
                assert response.status_code == 200
                assert response.json() == EXPECTED_LABEL_TYPES
                for label_type in EXPECTED_LABEL_TYPES:
                    response = client.get(f"/api/v2/label_type/{label_type['code']}")
                    assert response.status_code == 200
                    assert response.json() == label_type
                response = client.put("/api/v2/label_type/", json=new_type)
                assert response.status_code == 201
                assert response.json() == new_type
                assert client.put(
                    "/api/v2/label_type/", json={**new_type, "width": 200},
                ).status_code == 409
                assert client.get("/api/v2/label_type/0012345").json() == new_type
                assert client.get("/api/v2/label_type/9999999").status_code == 404
                paths = client.get("/api/v2/openapi.json").json()["paths"]
                assert {"get", "put"} <= paths["/api/v2/label_type/"].keys()
                assert "get" in paths["/api/v2/label_type/{code}"]
                response = client.put(
                    "/api/v2/label_type/",
                    content=(
                        '{"code":"123","description":"Invalid size",'
                        '"height":1e999,"width":100,"mirrored":false}'
                    ),
                    headers={"Content-Type": "application/json"},
                )
                assert response.status_code == 422
                assert response.json()["detail"][0]["type"] == "finite_number"
                assert client.get("/api/v2/label_type/123").status_code == 404

            insert = sql.SQL("""
                INSERT INTO {}.label_type (code, description, height, width, mirrored)
                VALUES (%s, %s, %s, %s, %s)
            """).format(sql.Identifier(schema))
            for index, value in enumerate(("code", "description", "height", "width", "mirrored")):
                invalid = ["1234567", "Test", 60, 100, False]
                invalid[index] = None
                with pytest.raises(errors.NotNullViolation):
                    connection.execute(insert, invalid)
            for index, value in [
                (0, "invalid"), (0, ""), (1, "  "),
                (2, 0), (2, -1), (2, float("inf")), (2, float("nan")),
                (3, 0), (3, -1), (3, float("inf")), (3, float("nan")),
            ]:
                invalid = ["1234567", "Test", 60, 100, False]
                invalid[index] = value
                with pytest.raises(errors.CheckViolation):
                    connection.execute(insert, invalid)

            connection.execute(sql.SQL("""
                UPDATE {}.label_type
                SET description = 'Customized', height = 70, width = 110, mirrored = true
                WHERE code = '4760100'
            """).format(sql.Identifier(schema)))
            expected = [
                {
                    **EXPECTED_LABEL_TYPES[0], "description": "Customized",
                    "height": 70, "width": 110, "mirrored": True,
                },
                EXPECTED_LABEL_TYPES[1],
                new_type,
            ]
            with TestClient(main.app) as client:
                response = client.get("/api/v2/label_type/")
                assert response.status_code == 200
                assert response.json() == sorted(expected, key=lambda item: item["code"])
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
