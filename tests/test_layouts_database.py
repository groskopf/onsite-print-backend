import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg import connect, errors, sql
from psycopg_pool import AsyncConnectionPool

from app import main
from app.db import INITIAL_LAYOUTS

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_CONNINFO"),
    reason="Set TEST_DATABASE_CONNINFO to an isolated PostgreSQL test database",
)

EXPECTED_LAYOUTS = [
    "layout_1",
    "layout_2",
    "layout_2PT",
    "layout_2PB",
    "layout_2PTL",
    "layout_2PTR",
    "layout_2PBL",
    "layout_2PBR",
    "layout_2QT",
    "layout_2QB",
    "layout_2QTL",
    "layout_2QTR",
    "layout_2QBL",
    "layout_2QBR",
    "layout_2PTLQ",
    "layout_2PTRQ",
    "layout_2PBLQ",
    "layout_2PBRQ",
    "layout_3",
    "layout_3PT",
    "layout_3PB",
    "layout_3PTL",
    "layout_3PTR",
    "layout_3PBL",
    "layout_3PBR",
    "layout_3PTLQ",
    "layout_3PTRQ",
    "layout_3PBLQ",
    "layout_3PBRQ",
    "layout_3QT",
    "layout_3QB",
    "layout_3QTL",
    "layout_3QTR",
    "layout_3QBL",
    "layout_3QBR",
]


def test_layout_seeds_associations_constraints_and_restart(monkeypatch):
    schema = f"layout_test_{uuid4().hex}"
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
            connection.execute(sql.SQL("""
                CREATE TABLE {}.layout (
                    name TEXT PRIMARY KEY CHECK (length(trim(name)) > 0),
                    position INTEGER NOT NULL CHECK (position >= 0)
                )
            """).format(sql.Identifier(schema)))
            connection.execute(
                sql.SQL("INSERT INTO {}.layout (name, position) VALUES ('layout_1', 0)").format(sql.Identifier(schema))
            )
            with TestClient(main.app) as client:
                response = client.get("/api/v2/layouts/")
                assert response.status_code == 200
                assert response.json() == EXPECTED_LAYOUTS
                assert len(response.json()) == len(set(response.json())) == 35
                for code in ("4760100", "4786103"):
                    response = client.get(f"/api/v2/nametag_types/{code}/layouts")
                    assert response.status_code == 200
                    assert response.json() == EXPECTED_LAYOUTS
                connection.execute(sql.SQL("""
                    INSERT INTO {}.nametag_type (code, description, height, width, folded)
                    VALUES ('00123', 'Custom', 60, 100, false)
                """).format(sql.Identifier(schema)))
                response = client.get("/api/v2/nametag_types/00123/layouts")
                assert response.status_code == 200
                assert response.json() == []
                assert client.get("/api/v2/nametag_types/999/layouts").status_code == 404
                paths = client.get("/api/v2/openapi.json").json()["paths"]
                assert set(paths["/api/v2/layouts/"]) == {"get"}
                assert set(paths["/api/v2/nametag_types/{code}/layouts"]) == {"get"}

            descriptions = dict(
                connection.execute(
                    sql.SQL("SELECT name, description FROM {}.layout").format(sql.Identifier(schema))
                ).fetchall()
            )
            assert descriptions == dict(INITIAL_LAYOUTS)
            assert descriptions["layout_1"] == "En linie"
            assert all(descriptions.values())
            with pytest.raises(errors.CheckViolation):
                connection.execute(
                    sql.SQL("UPDATE {}.layout SET description = '  ' WHERE name = 'layout_1'").format(
                        sql.Identifier(schema)
                    )
                )

            association = sql.SQL("""
                INSERT INTO {}.nametag_type_layout (nametag_type_code, layout_name)
                VALUES (%s, %s)
            """).format(sql.Identifier(schema))
            for values in [("999", "layout_1"), ("00123", "unknown")]:
                with pytest.raises(errors.ForeignKeyViolation):
                    connection.execute(association, values)
            with pytest.raises(errors.UniqueViolation):
                connection.execute(association, ("4760100", "layout_1"))
            with pytest.raises(errors.CheckViolation):
                connection.execute(
                    sql.SQL("INSERT INTO {}.layout (name, position) VALUES (' ', 0)").format(sql.Identifier(schema))
                )
            with pytest.raises(errors.CheckViolation):
                connection.execute(
                    sql.SQL("INSERT INTO {}.layout (name, position) VALUES ('negative', -1)").format(
                        sql.Identifier(schema)
                    )
                )

            connection.execute(
                sql.SQL("INSERT INTO {}.layout (name, position) VALUES ('custom', 100)").format(sql.Identifier(schema))
            )
            connection.execute(association, ("00123", "custom"))
            connection.execute(
                sql.SQL(
                    "UPDATE {}.layout SET position = 99, description = 'Customized' WHERE name = 'layout_1'"
                ).format(sql.Identifier(schema))
            )
            expected = EXPECTED_LAYOUTS[1:] + ["layout_1"]
            for _ in range(2):
                with TestClient(main.app) as client:
                    assert client.get("/api/v2/layouts/").json() == expected + ["custom"]
                    assert client.get(
                        "/api/v2/nametag_types/00123/layouts",
                    ).json() == ["custom"]
                    assert (
                        client.get(
                            "/api/v2/nametag_types/4760100/layouts",
                        ).json()
                        == expected
                    )
                assert connection.execute(
                    sql.SQL("SELECT description FROM {}.layout WHERE name = 'layout_1'").format(sql.Identifier(schema))
                ).fetchone() == ("Customized",)
                assert connection.execute(
                    sql.SQL("SELECT description FROM {}.layout WHERE name = 'custom'").format(sql.Identifier(schema))
                ).fetchone() == (None,)
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
