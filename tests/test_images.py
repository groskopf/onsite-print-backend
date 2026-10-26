from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfReader

from app import endpoint_images
from app.db import get_connection
from app.endpoint_images import MAX_IMAGE_BYTES, router
from app.endpoint_nametags import router as nametags_router
from app.endpoint_sheets import router as sheets_router
from app.main import API_PREFIX, app

CODE = "ABCDEFGHIJKLMNOP"
OTHER_CODE = "QRSTUVWXYZ234567"
BOOKING_CODE = "AAAAAAAAAAAAAAAA"
URL = f"{API_PREFIX}/images/{CODE}"


class FakeCursor:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, query, params):
        self.query = query
        self.params = params

    async def fetchone(self):
        if "FROM booking" in self.query:
            if self.params != (BOOKING_CODE, CODE):
                return None
            return {"nametag_type": "4760100", "event_code": CODE, "active": True}
        if "FROM event" in self.query:
            return (1,) if self.params[0] in (CODE, OTHER_CODE) else None
        return (1,)


class FakeConnection:
    def cursor(self, **kwargs):
        return FakeCursor()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGES_DIR", str(tmp_path / "images"))
    monkeypatch.setenv("SHEETS_DIR", str(tmp_path / "sheets"))
    monkeypatch.setenv("NAMETAGS_DIR", str(tmp_path / "nametags"))

    async def fake_connection():
        yield FakeConnection()

    application = FastAPI()
    for resource_router in (router, sheets_router, nametags_router):
        application.include_router(resource_router, prefix=API_PREFIX)
    application.dependency_overrides[get_connection] = fake_connection
    with TestClient(application) as client:
        yield client, tmp_path


def image_bytes(image_format="PNG"):
    output = BytesIO()
    Image.new("RGB", (20, 10), "red").save(output, format=image_format)
    return output.getvalue()


def upload(client, content=None, url=URL):
    return client.post(
        url,
        files={"image": ("../../untrusted.svg", image_bytes() if content is None else content, "text/plain")},
    )


@pytest.mark.parametrize(
    "image_format,extension,media_type",
    [
        ("PNG", "png", "image/png"),
        ("JPEG", "jpg", "image/jpeg"),
        ("GIF", "gif", "image/gif"),
        ("WEBP", "webp", "image/webp"),
        ("BMP", "bmp", "image/bmp"),
        ("TIFF", "tiff", "image/tiff"),
    ],
)
def test_upload_list_download_delete(setup, image_format, extension, media_type):
    client, root = setup
    content = image_bytes(image_format)
    response = upload(client, content)
    assert response.status_code == 201
    filename = response.json()["filename"]
    assert filename.endswith("." + extension)
    assert "/" not in filename
    assert (root / "images" / CODE / filename).read_bytes() == content
    assert client.get(URL).json() == [{"filename": filename}]
    download = client.get(f"{URL}/{filename}")
    assert download.content == content
    assert download.headers["content-type"] == media_type
    assert client.delete(f"{URL}/{filename}").json() == {"filename": filename}
    assert client.get(URL).json() == []
    for method in (client.get, client.delete):
        response = method(f"{URL}/{filename}")
        assert response.status_code == 404
        assert response.json() == {"detail": "Image not found"}


def test_uploads_are_unique_and_event_scoped(setup):
    client, root = setup
    first = upload(client).json()["filename"]
    second = upload(client).json()["filename"]
    assert first != second
    assert client.get(URL).json() == [{"filename": first}, {"filename": second}]
    other_url = f"{API_PREFIX}/images/{OTHER_CODE}"
    assert client.get(other_url).json() == []
    assert client.get(f"{other_url}/{first}").status_code == 404
    assert client.delete(f"{other_url}/{first}").status_code == 404
    assert (root / "images" / CODE / first).exists()


@pytest.mark.parametrize("extra_bytes,expected_status", [(0, 201), (1, 413)])
def test_exact_upload_size_limit(setup, extra_bytes, expected_status):
    client, root = setup
    content = image_bytes()
    content += b"\0" * (MAX_IMAGE_BYTES + extra_bytes - len(content))
    response = upload(client, content)
    assert response.status_code == expected_status
    files = list((root / "images" / CODE).iterdir())
    assert len(files) == (1 if expected_status == 201 else 0)
    if expected_status == 413:
        assert response.json() == {"detail": "Image exceeds 10 MiB"}


@pytest.mark.parametrize("content", [b"", b"not an image", b"<svg></svg>", image_bytes()[:40]])
def test_invalid_uploads_leave_no_files(setup, content):
    client, root = setup
    response = upload(client, content)
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid image"}
    assert list((root / "images" / CODE).iterdir()) == []


def test_unsupported_image_format(setup):
    client, root = setup
    response = upload(client, image_bytes("PPM"))
    assert response.status_code == 415
    assert list((root / "images" / CODE).iterdir()) == []


def test_image_dimensions_are_bounded(setup, monkeypatch):
    client, root = setup
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    assert upload(client).status_code == 400
    assert list((root / "images" / CODE).iterdir()) == []


def test_failed_publication_cleans_temporary_file(setup, monkeypatch):
    client, root = setup

    def broken_replace(*args):
        raise OSError("Storage unavailable")

    monkeypatch.setattr(endpoint_images.os, "replace", broken_replace)
    with pytest.raises(OSError, match="Storage unavailable"):
        upload(client)
    assert list((root / "images" / CODE).iterdir()) == []


def test_unknown_event_and_missing_upload(setup):
    client, root = setup
    unknown_url = f"{API_PREFIX}/images/BBBBBBBBBBBBBBBB"
    for response in (upload(client, url=unknown_url), client.get(unknown_url)):
        assert response.status_code == 404
        assert response.json() == {"detail": "Event not found"}
    assert client.get(f"{unknown_url}/a.png").status_code == 404
    assert client.delete(f"{unknown_url}/a.png").status_code == 404
    assert upload(client, url=f"{API_PREFIX}/images/bad-code").status_code == 422
    assert client.post(URL).status_code == 422
    assert not (root / "images").exists()


@pytest.mark.parametrize("filename", ["notes.txt", ".hidden.png", "x.png.tmp", r"..\x.png", "..%2Fx.png"])
def test_invalid_filenames(setup, filename):
    client, root = setup
    for method in (client.get, client.delete):
        assert method(f"{URL}/{filename}").status_code in (404, 422)


def test_listing_ignores_symlinks_and_temporary_files(setup):
    client, root = setup
    filename = upload(client).json()["filename"]
    directory = root / "images" / CODE
    (directory / "link.png").symlink_to(directory / filename)
    (directory / ".partial.tmp").write_bytes(b"data")
    (directory / "notes.txt").write_bytes(b"data")
    (directory / "folder.png").mkdir()
    assert client.get(URL).json() == [{"filename": filename}]
    for method in (client.get, client.delete):
        assert method(f"{URL}/link.png").status_code == 404
        assert method(f"{URL}/folder.png").status_code == 404
    assert (directory / filename).exists()


def test_event_directory_cannot_be_a_symlink(setup):
    client, root = setup
    images = root / "images"
    images.mkdir()
    outside = root / "outside"
    outside.mkdir()
    (images / CODE).symlink_to(outside, target_is_directory=True)
    assert upload(client).status_code == 404
    assert client.get(URL).status_code == 404
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("image_format", ["PNG", "JPEG", "GIF", "WEBP", "BMP", "TIFF"])
def test_uploaded_image_renders_in_nametag_and_sheet(setup, image_format):
    client, root = setup
    filename = upload(client, image_bytes(image_format)).json()["filename"]
    name = {"line_1": "Ada", "image_name": filename}
    nametag = client.post(f"{API_PREFIX}/nametags/{CODE}/{BOOKING_CODE}", json={"layout": "layout_2PT", **name})
    assert nametag.status_code == 201
    sheet = client.post(
        f"{API_PREFIX}/sheets/{CODE}",
        json={"sheet_type": "456090", "layout": "layout_1", "name_data_list": [name]},
    )
    assert sheet.status_code == 201
    for path in (
        root / "nametags" / CODE / BOOKING_CODE / nametag.json()["filename"],
        root / "sheets" / CODE / sheet.json()["filename"],
    ):
        page = PdfReader(path).pages[0]
        assert any(operator == b"Do" for _, operator in page.get_contents().operations)


def test_images_from_other_events_and_global_directory_are_not_used(setup):
    client, root = setup
    filename = upload(client, url=f"{API_PREFIX}/images/{OTHER_CODE}").json()["filename"]
    (root / "images" / filename).write_bytes(image_bytes())
    name = {"line_1": "Ada", "image_name": filename}
    nametag = client.post(f"{API_PREFIX}/nametags/{CODE}/{BOOKING_CODE}", json={"layout": "layout_2PT", **name})
    sheet = client.post(
        f"{API_PREFIX}/sheets/{CODE}",
        json={"sheet_type": "456090", "layout": "layout_1", "name_data_list": [name]},
    )
    for response in (nametag, sheet):
        assert response.status_code == 404
        assert response.json() == {"detail": "Image not found"}
    assert not list((root / "nametags").rglob("*.pdf"))
    assert not list((root / "sheets").rglob("*.pdf"))


def test_deleted_images_are_not_available_for_new_pdfs(setup):
    client, root = setup
    filename = upload(client).json()["filename"]
    assert client.delete(f"{URL}/{filename}").status_code == 200
    response = client.post(
        f"{API_PREFIX}/nametags/{CODE}/{BOOKING_CODE}",
        json={"layout": "layout_1", "line_1": "Ada", "image_name": filename},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Image not found"}
    assert not list((root / "nametags").rglob("*.pdf"))


def test_image_openapi_contract():
    schema = app.openapi()
    path = f"{API_PREFIX}/images/{{event_code}}"
    assert set(schema["paths"][path]) == {"post", "get"}
    assert set(schema["paths"][path + "/{filename}"]) == {"get", "delete"}
    upload_schema = schema["paths"][path]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]
    model = schema["components"]["schemas"][upload_schema["$ref"].split("/")[-1]]
    assert model["required"] == ["image"]
