import os
from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app import sheet_pdf
from app.db import get_connection
from app.endpoint_sheets import router
from app.main import API_PREFIX, app

CODE = "ABCDEFGHIJKLMNOP"
URL = f"{API_PREFIX}/sheets/{CODE}"
OTHER_CODE = "QRSTUVWXYZ234567"
BODY = {
    "sheet_type": "456090",
    "layout": "layout_1",
    "name_data_list": [{"line_1": "  Ada Lovelace  ", "line_3": "Analytical Engines"}],
}


class FakeCursor:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, query, params):
        self.event_code = params[0]

    async def fetchone(self):
        return (1,) if self.event_code in (CODE, OTHER_CODE) else None


class FakeConnection:
    def cursor(self):
        return FakeCursor()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "sheets"
    monkeypatch.setenv("SHEETS_DIR", str(root))
    monkeypatch.setenv("IMAGES_DIR", str(tmp_path / "images"))

    async def fake_connection():
        yield FakeConnection()

    application = FastAPI()
    application.include_router(router, prefix=API_PREFIX)
    application.dependency_overrides[get_connection] = fake_connection
    with TestClient(application) as client:
        yield client, root / CODE


def test_sheet_routes_registered_with_event_dependencies():
    schema = app.openapi()
    collection = f"{API_PREFIX}/sheets/{{event_code}}"
    assert set(schema["paths"][collection]) == {"post", "get", "delete"}
    assert set(schema["paths"][collection + "/{filename}"]) == {"get", "delete"}
    for route in router.routes:
        assert len(route.dependant.dependencies) == 1


def test_generate_download_and_delete(setup):
    client, root = setup
    response = client.post(URL, json=BODY)
    assert response.status_code == 201
    filename = response.json()["filename"]
    assert filename.startswith("456090_layout_1_") and filename.endswith(".pdf")
    assert [path.name for path in root.iterdir()] == [filename]
    assert client.get(URL).json() == [{"filename": filename}]
    download = client.get(URL + "/" + filename)
    assert download.headers["content-type"] == "application/pdf"
    assert download.content == (root / filename).read_bytes()
    assert "Ada Lovelace" in PdfReader(BytesIO(download.content)).pages[0].extract_text()
    assert client.delete(URL + "/" + filename).json() == {"filename": filename}
    assert client.get(URL).json() == []
    for method in (client.get, client.delete):
        response = method(URL + "/" + filename)
        assert response.status_code == 404
        assert response.json() == {"detail": "Sheet not found"}


def test_repeated_requests_generate_unique_files(setup):
    client, root = setup
    filenames = [client.post(URL, json=BODY).json()["filename"] for _ in range(3)]
    assert len(set(filenames)) == 3
    assert len(list(root.iterdir())) == 3


@pytest.mark.parametrize(
    "body",
    [
        {},
        {**BODY, "sheet_type": "../456090"},
        {**BODY, "booking_code": "ABCDEFGHIJKLMNOP"},
        {**BODY, "name_data_list": []},
        {**BODY, "name_data_list": [{}]},
        {**BODY, "name_data_list": [{"line_1": None}]},
        {**BODY, "name_data_list": [{"line_1": "Ada", "layout": "layout_2"}]},
        {**BODY, "name_data_list": [{"line_1": "Ada", "unknown": "field"}]},
    ],
)
def test_invalid_requests_do_not_create_files(setup, body):
    client, root = setup
    assert client.post(URL, json=body).status_code == 422
    assert not root.exists()


@pytest.mark.parametrize(
    "body,status_code,detail",
    [
        ({**BODY, "sheet_type": "453060"}, 404, "Name tag sheet type not supported"),
        ({**BODY, "layout": "layout_2"}, 400, "Name tag sheet layout not supported"),
        ({**BODY, "layout": "../outside"}, 400, "Name tag sheet layout not supported"),
        ({**BODY, "name_data_list": [{"line_1": "Ada", "image_name": "missing.png"}]}, 404, "Image not found"),
    ],
)
def test_renderer_input_errors(setup, body, status_code, detail):
    client, root = setup
    response = client.post(URL, json=body)
    assert response.status_code == status_code
    assert response.json() == {"detail": detail}
    assert not root.exists() or list(root.iterdir()) == []


def test_failed_render_leaves_no_files(setup, monkeypatch):
    client, root = setup

    def broken(path, sheet_data, event_code):
        assert event_code == CODE
        path.write_bytes(b"partial")
        raise RuntimeError("Render failed")

    monkeypatch.setattr(sheet_pdf, "render_sheet", broken)
    with pytest.raises(RuntimeError, match="Render failed"):
        client.post(URL, json=BODY)
    assert list(root.iterdir()) == []


def test_list_delete_all_are_ordered_and_preserve_other_entries(setup, tmp_path):
    client, root = setup
    assert client.get(URL).json() == []
    assert client.delete(URL).json() == []
    root.mkdir(parents=True)
    for name, mtime in (
        ("b.pdf", 1_000),
        ("a.pdf", 2_000),
        ("c.pdf", 1_000),
        (".partial.pdf.tmp", 0),
        ("notes.txt", 0),
    ):
        path = root / name
        path.write_bytes(b"data")
        os.utime(path, ns=(mtime, mtime))
    (root / "folder.pdf").mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside")
    (root / "link.pdf").symlink_to(outside)
    expected = [{"filename": name} for name in ("b.pdf", "c.pdf", "a.pdf")]
    assert client.get(URL).json() == expected
    assert client.delete(URL).json() == expected
    assert sorted(path.name for path in root.iterdir()) == [".partial.pdf.tmp", "folder.pdf", "link.pdf", "notes.txt"]
    assert outside.read_bytes() == b"outside"
    assert client.get(URL).json() == []
    for method in (client.get, client.delete):
        assert method(URL + "/link.pdf").status_code == 404
        assert method(URL + "/folder.pdf").status_code == 404


@pytest.mark.parametrize("filename", ["notes.txt", ".hidden.pdf", "a.pdf.tmp", "a%20b.pdf", "..%2Foutside.pdf"])
def test_invalid_filenames(setup, filename):
    client, root = setup
    for method in (client.get, client.delete):
        assert method(URL + "/" + filename).status_code in (404, 422)


def test_sheet_files_are_isolated_by_event(setup):
    client, root = setup
    filename = client.post(URL, json=BODY).json()["filename"]
    other_url = f"{API_PREFIX}/sheets/{OTHER_CODE}"
    assert client.get(other_url).json() == []
    assert client.get(f"{other_url}/{filename}").status_code == 404
    assert client.delete(f"{other_url}/{filename}").status_code == 404
    assert client.delete(other_url).json() == []
    assert (root / filename).exists()


def test_missing_event_does_not_create_sheet(setup):
    client, root = setup
    response = client.post(f"{API_PREFIX}/sheets/AAAAAAAAAAAAAAAA", json=BODY)
    assert response.status_code == 404
    assert response.json() == {"detail": "Event not found"}
    assert not root.parent.exists()


def test_invalid_event_code(setup):
    client, root = setup
    assert client.post(f"{API_PREFIX}/sheets/bad-code", json=BODY).status_code == 422
    assert not root.parent.exists()
