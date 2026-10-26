import os
from datetime import date, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg import connect, sql
from psycopg_pool import AsyncConnectionPool

from app import main, nametag_pdf

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_CONNINFO"),
    reason="Set TEST_DATABASE_CONNINFO to an isolated PostgreSQL test database",
)


def test_nametag_files_for_booking(monkeypatch, tmp_path):
    schema = f"nametag_test_{uuid4().hex}"
    conninfo = os.environ["TEST_DATABASE_CONNINFO"]
    monkeypatch.setenv("NAMETAGS_DIR", str(tmp_path))
    monkeypatch.setattr(
        nametag_pdf,
        "render_nametag",
        lambda path, nametag_type, name_data, event_code: path.write_bytes(
            f"%PDF {nametag_type} {name_data.line_1}".encode()
        ),
    )
    with connect(conninfo, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            monkeypatch.setattr(
                main,
                "create_pool",
                lambda: AsyncConnectionPool(conninfo, open=False, kwargs={"options": f"-c search_path={schema}"}),
            )
            today = date.today()
            with TestClient(main.app) as client:
                event_code = client.post(
                    "/api/v2/events/",
                    json={
                        "name": "Print conference",
                        "start_date": str(today),
                        "end_date": str(today),
                    },
                ).json()["code"]

                def booking(printer_code, start, end):
                    return client.post(
                        "/api/v2/bookings/",
                        json={
                            "event_code": event_code,
                            "printer_code": printer_code,
                            "nametag_type": "4786103",
                            "start_date": str(start),
                            "end_date": str(end),
                        },
                    ).json()["booking_code"]

                active = booking("1OPYKBGXVN_1", today, today)
                future = booking("YF1OF5FWZ0_2", today + timedelta(days=1), today + timedelta(days=2))
                body = {"layout": "layout_3", "line_1": "Ada"}

                base_url = f"/api/v2/nametags/{event_code}"
                response = client.post(f"{base_url}/{active}", json=body)
                assert response.status_code == 201
                filename = response.json()["filename"]
                assert filename.startswith("4786103_layout_3_")
                assert (tmp_path / event_code / active / filename).read_bytes() == b"%PDF 4786103 Ada"
                assert client.get(f"{base_url}/{active}").json() == [{"filename": filename}]
                assert client.get(f"{base_url}/{active}/{filename}").content == b"%PDF 4786103 Ada"

                assert client.post(f"{base_url}/{future}", json=body).json() == {
                    "detail": "Booking not valid today",
                }
                assert client.post(f"{base_url}/AAAAAAAAAAAAAAAA", json=body).status_code == 404
                response = client.post(f"{base_url}/{active}", json={**body, "layout": "unknown"})
                assert response.status_code == 400
                assert response.json() == {"detail": "Name tag layout not supported"}

                other_event = client.post(
                    "/api/v2/events/",
                    json={"name": "Other event", "start_date": str(today), "end_date": str(today)},
                ).json()["code"]
                wrong_url = f"/api/v2/nametags/{other_event}/{active}"
                for response in (
                    client.post(wrong_url, json=body),
                    client.get(wrong_url),
                    client.delete(wrong_url),
                    client.get(f"{wrong_url}/{filename}"),
                    client.delete(f"{wrong_url}/{filename}"),
                ):
                    assert response.status_code == 404
                    assert response.json() == {"detail": "Booking not found"}
                assert (tmp_path / event_code / active / filename).exists()
                assert client.delete(f"{base_url}/{active}").json() == [{"filename": filename}]
                assert not (tmp_path / event_code / active).exists()
                assert not (tmp_path / event_code / future).exists()
                paths = client.get("/api/v2/openapi.json").json()["paths"]
                assert set(paths["/api/v2/nametags/{event_code}/{booking_code}"]) == {"get", "post", "delete"}
                assert set(paths["/api/v2/nametags/{event_code}/{booking_code}/{filename}"]) == {"get", "delete"}
            tables = connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
                (schema,),
            ).fetchall()
            assert not any(
                "nametag" in name and name not in ("nametag_type", "nametag_type_layout") for (name,) in tables
            )
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
