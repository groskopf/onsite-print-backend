import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app import nametag_pdf
from app.db import get_connection
from app.endpoint_nametags import router
from app.main import API_PREFIX

CODE = "ABCDEFGHIJKLMNOP"
EVENT_CODE = "QRSTUVWXYZ234567"
URL = f"{API_PREFIX}/nametags/{EVENT_CODE}/{CODE}"
BODY = {"layout": "layout_2", "line_1": "  Ada Lovelace  ", "line_2": "Analytical Engines"}


class FakeCursor:
    def __init__(self):
        self.results = []
        self.queries = []
        self.booking_exists = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, query, params=None):
        self.queries.append((query, params))

    async def fetchone(self):
        if self.results:
            return self.results.pop(0)
        if "FROM booking" in self.queries[-1][0] and self.booking_exists and self.queries[-1][1] == (CODE, EVENT_CODE):
            return {"nametag_type": "4760100", "event_code": EVENT_CODE, "active": True}
        return None


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def cursor(self, *, row_factory):
        return self.fake_cursor


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("NAMETAGS_DIR", str(tmp_path))
    cursor = FakeCursor()

    async def fake_connection():
        yield FakeConnection(cursor)

    app = FastAPI()
    app.include_router(router, prefix=API_PREFIX)
    app.dependency_overrides[get_connection] = fake_connection
    with TestClient(app) as client:
        yield client, cursor, tmp_path / EVENT_CODE


def fake_render(calls):
    def render(path, nametag_type, name_data, event_code):
        calls.append((path, nametag_type, name_data, event_code))
        path.write_bytes(b"%PDF-1.4 " + name_data.line_1.encode())

    return render


def add_file(directory, name, mtime):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"%PDF " + name.encode())
    os.utime(path, ns=(mtime, mtime))
    return path


def test_create_nametag(setup, monkeypatch):
    client, cursor, root = setup
    calls = []
    monkeypatch.setattr(nametag_pdf, "render_nametag", fake_render(calls))
    cursor.results = [{"nametag_type": "4760100", "event_code": EVENT_CODE, "active": True}, {"?column?": 1}]
    response = client.post(URL, json=BODY)
    assert response.status_code == 201
    filename = response.json()["filename"]
    assert filename.startswith("4760100_layout_2_") and filename.endswith(".pdf")
    assert (root / CODE / filename).read_bytes() == b"%PDF-1.4 Ada Lovelace"
    assert [path.name for path in (root / CODE).iterdir()] == [filename]
    assert calls[0][1] == "4760100"
    assert calls[0][2].line_1 == "Ada Lovelace"
    assert calls[0][3] == EVENT_CODE
    assert cursor.queries[0][1] == (CODE, EVENT_CODE)
    assert cursor.queries[1][1] == ("4760100", "layout_2")
    assert client.get(URL).json() == [{"filename": filename}]


@pytest.mark.parametrize(
    ("results", "status_code", "detail"),
    [
        ([], 404, "Booking not found"),
        ([{"nametag_type": "4760100", "event_code": EVENT_CODE, "active": False}], 400, "Booking not valid today"),
        ([{"nametag_type": "4760100", "event_code": EVENT_CODE, "active": True}], 400, "Name tag layout not supported"),
    ],
)
def test_create_nametag_rejected(setup, monkeypatch, results, status_code, detail):
    client, cursor, root = setup
    calls = []
    monkeypatch.setattr(nametag_pdf, "render_nametag", fake_render(calls))
    cursor.results = results
    cursor.booking_exists = bool(results)
    response = client.post(URL, json=BODY)
    assert response.status_code == status_code
    assert response.json() == {"detail": detail}
    assert calls == []
    assert not (root / CODE).exists()


def test_generate_download_and_delete_pdf(setup):
    client, cursor, root = setup
    cursor.results = [{"nametag_type": "4760100", "event_code": EVENT_CODE, "active": True}, {"?column?": 1}]
    response = client.post(URL, json=BODY)
    assert response.status_code == 201
    filename = response.json()["filename"]
    pdf = PdfReader(root / CODE / filename)
    assert len(pdf.pages) == 1
    assert "Ada Lovelace" in pdf.pages[0].extract_text()
    download = client.get(f"{URL}/{filename}")
    assert download.headers["content-type"] == "application/pdf"
    assert download.content == (root / CODE / filename).read_bytes()
    assert client.delete(f"{URL}/{filename}").json() == {"filename": filename}
    assert client.get(URL).json() == []


@pytest.mark.parametrize(
    ("nametag_type", "layout", "image_name", "status_code", "detail"),
    [
        ("9999999", "layout_2", None, 404, "NameTagType not supported"),
        ("4760100", "custom", None, 400, "Name tag layout not supported"),
        ("4760100", "layout_2PT", "missing.png", 404, "Image not found"),
        ("4760100", "layout_1", "../outside.png", 404, "Image not found"),
    ],
)
def test_renderer_input_errors(setup, nametag_type, layout, image_name, status_code, detail):
    client, cursor, root = setup
    cursor.results = [{"nametag_type": nametag_type, "event_code": EVENT_CODE, "active": True}, {"?column?": 1}]
    response = client.post(URL, json={**BODY, "layout": layout, "image_name": image_name})
    assert response.status_code == status_code
    assert response.json() == {"detail": detail}
    assert list((root / CODE).iterdir()) == []


def test_failed_render_leaves_no_file(setup, monkeypatch):
    client, cursor, root = setup

    def broken(path, nametag_type, name_data, event_code):
        path.write_bytes(b"partial")
        raise RuntimeError("render failed")

    monkeypatch.setattr(nametag_pdf, "render_nametag", broken)
    cursor.results = [{"nametag_type": "4760100", "event_code": EVENT_CODE, "active": True}, {"?column?": 1}]
    with pytest.raises(RuntimeError, match="render failed"):
        client.post(URL, json=BODY)
    assert list((root / CODE).iterdir()) == []


@pytest.mark.parametrize(
    "body",
    [
        {"line_1": "Ada"},
        {"layout": "layout_1"},
        {**BODY, "line_1": None},
        {**BODY, "unknown": "x"},
    ],
)
def test_invalid_name_data(setup, body):
    client, cursor, root = setup
    assert client.post(URL, json=body).status_code == 422
    assert all("FROM booking" in query for query, _ in cursor.queries)
    assert not root.exists()


def test_list_and_delete_all(setup):
    client, cursor, root = setup
    directory = root / CODE
    add_file(directory, "b.pdf", 2_000)
    add_file(directory, "a.pdf", 3_000)
    add_file(directory, "c.pdf", 1_000)
    add_file(directory, ".c.pdf.tmp", 1_000)
    add_file(directory, "notes.txt", 1_000)
    (directory / "sub.pdf").mkdir()
    add_file(root / "QRSTUVWXYZ234567", "other.pdf", 1_000)
    expected = [{"filename": name} for name in ("c.pdf", "b.pdf", "a.pdf")]
    assert client.get(URL).json() == expected
    response = client.delete(URL)
    assert response.status_code == 200
    assert response.json() == expected
    assert sorted(path.name for path in directory.iterdir()) == [".c.pdf.tmp", "notes.txt", "sub.pdf"]
    assert (root / "QRSTUVWXYZ234567" / "other.pdf").exists()
    assert client.get(URL).json() == []


def test_delete_all_removes_empty_directory(setup):
    client, cursor, root = setup
    add_file(root / CODE, "a.pdf", 1_000)
    assert client.delete(URL).json() == [{"filename": "a.pdf"}]
    assert not (root / CODE).exists()


def test_missing_directory_is_empty(setup):
    client, cursor, root = setup
    assert client.get(URL).json() == []
    assert client.delete(URL).json() == []


def test_get_and_delete_one(setup):
    client, cursor, root = setup
    add_file(root / CODE, "a.pdf", 1_000)
    add_file(root / CODE, "b.pdf", 2_000)
    response = client.get(f"{URL}/a.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == b"%PDF a.pdf"
    response = client.delete(f"{URL}/a.pdf")
    assert response.status_code == 200
    assert response.json() == {"filename": "a.pdf"}
    assert client.get(URL).json() == [{"filename": "b.pdf"}]
    for method in ("get", "delete"):
        response = getattr(client, method)(f"{URL}/a.pdf")
        assert response.status_code == 404
        assert response.json() == {"detail": "Nametag not found"}


def test_symlinks_are_not_served(setup, tmp_path_factory):
    client, cursor, root = setup
    outside = tmp_path_factory.mktemp("outside") / "secret.pdf"
    outside.write_bytes(b"secret")
    (root / CODE).mkdir(parents=True)
    (root / CODE / "link.pdf").symlink_to(outside)
    assert client.get(URL).json() == []
    assert client.get(f"{URL}/link.pdf").status_code == 404
    assert client.delete(f"{URL}/link.pdf").status_code == 404
    assert outside.exists()


@pytest.mark.parametrize(
    "filename",
    [
        "a.txt",
        ".hidden.pdf",
        "a.pdf.tmp",
        "..%2Fsecret.pdf",
        "a%20b.pdf",
        "%2Fetc%2Fpasswd",
    ],
)
@pytest.mark.parametrize("method", ["get", "delete"])
def test_invalid_filenames(setup, method, filename):
    client, cursor, root = setup
    assert getattr(client, method)(f"{URL}/{filename}").status_code in (404, 422)


@pytest.mark.parametrize("code", ["abcdefghijklmnop", "ABCDEFGHIJKLMNO", "ABCDEFGHIJKLMNO1", "ABCDEFGHIJKLMNOPQ"])
def test_invalid_booking_codes(setup, code):
    client, cursor, root = setup
    url = f"{API_PREFIX}/nametags/{EVENT_CODE}/{code}"
    assert client.get(url).status_code == 422
    assert client.delete(url).status_code == 422
    assert client.post(url, json=BODY).status_code == 422
    assert client.get(f"{url}/a.pdf").status_code == 422
    assert client.delete(f"{url}/a.pdf").status_code == 422


@pytest.mark.parametrize("code", ["bad-code", "abcdefghijklmnop", "ABCDEFGHIJKLMNO1"])
def test_invalid_event_codes(setup, code):
    client, cursor, root = setup
    url = f"{API_PREFIX}/nametags/{code}/{CODE}"
    for response in (
        client.post(url, json=BODY),
        client.get(url),
        client.delete(url),
        client.get(f"{url}/a.pdf"),
        client.delete(f"{url}/a.pdf"),
    ):
        assert response.status_code == 422
    assert cursor.queries == []


def test_missing_booking_or_wrong_event_rejects_all_operations(setup):
    client, cursor, root = setup
    add_file(root / CODE, "a.pdf", 1_000)
    cursor.booking_exists = False
    for response in (
        client.post(URL, json=BODY),
        client.get(URL),
        client.delete(URL),
        client.get(f"{URL}/a.pdf"),
        client.delete(f"{URL}/a.pdf"),
    ):
        assert response.status_code == 404
        assert response.json() == {"detail": "Booking not found"}
    assert (root / CODE / "a.pdf").exists()
    assert all(params == (CODE, EVENT_CODE) for _, params in cursor.queries)
    assert all("b.event_code = %s" in query for query, _ in cursor.queries)


def test_wrong_event_cannot_access_or_modify_booking_files(setup):
    client, cursor, root = setup
    original = add_file(root / CODE, "a.pdf", 1_000)
    wrong_url = f"{API_PREFIX}/nametags/AAAAAAAAAAAAAAAA/{CODE}"
    for response in (
        client.post(wrong_url, json=BODY),
        client.get(wrong_url),
        client.delete(wrong_url),
        client.get(f"{wrong_url}/a.pdf"),
        client.delete(f"{wrong_url}/a.pdf"),
    ):
        assert response.status_code == 404
        assert response.json() == {"detail": "Booking not found"}
    assert original.read_bytes() == b"%PDF a.pdf"
    assert not (root.parent / "AAAAAAAAAAAAAAAA").exists()


def test_repeated_generation_preserves_unique_files(setup):
    client, cursor, root = setup
    filenames = []
    for _ in range(2):
        cursor.results = [{"nametag_type": "4760100", "event_code": EVENT_CODE, "active": True}, (1,)]
        response = client.post(URL, json=BODY)
        assert response.status_code == 201
        filenames.append(response.json()["filename"])
    assert len(set(filenames)) == 2
    assert sorted(path.name for path in (root / CODE).iterdir()) == sorted(filenames)


def test_inactive_booking_can_still_read_and_delete_files(setup):
    client, cursor, root = setup
    add_file(root / CODE, "a.pdf", 1_000)
    for method, suffix in ((client.get, ""), (client.get, "/a.pdf"), (client.delete, "/a.pdf")):
        cursor.results = [{"nametag_type": "4760100", "event_code": EVENT_CODE, "active": False}]
        assert method(URL + suffix).status_code == 200


def test_legacy_files_remain_untouched_and_unlisted(setup):
    client, cursor, root = setup
    legacy = add_file(root.parent / CODE, "legacy.pdf", 1_000)
    assert client.get(URL).json() == []
    assert client.delete(URL).json() == []
    assert client.get(f"{URL}/legacy.pdf").status_code == 404
    assert legacy.read_bytes() == b"%PDF legacy.pdf"


@pytest.mark.parametrize("level", ["event", "booking"])
def test_directory_symlinks_are_rejected(setup, tmp_path, level):
    client, cursor, root = setup
    outside = tmp_path / "outside"
    outside.mkdir()
    add_file(outside, "a.pdf", 1_000)
    link = root if level == "event" else root / CODE
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    cursor.results = [{"nametag_type": "4760100", "event_code": EVENT_CODE, "active": True}, (1,)]
    for response in (
        client.post(URL, json=BODY),
        client.get(URL),
        client.delete(URL),
        client.get(f"{URL}/a.pdf"),
        client.delete(f"{URL}/a.pdf"),
    ):
        assert response.status_code == 404
    assert (outside / "a.pdf").exists()
    assert len(list(outside.iterdir())) == 1


def test_openapi_uses_event_and_booking_paths():
    app = FastAPI()
    app.include_router(router, prefix=API_PREFIX)
    paths = app.openapi()["paths"]
    collection = f"{API_PREFIX}/nametags/{{event_code}}/{{booking_code}}"
    assert set(paths[collection]) == {"get", "post", "delete"}
    assert set(paths[collection + "/{filename}"]) == {"get", "delete"}
    assert f"{API_PREFIX}/nametags/{{booking_code}}" not in paths
