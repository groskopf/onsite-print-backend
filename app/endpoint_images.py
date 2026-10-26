import os
import uuid
import warnings
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException
from fastapi import Path as PathParam
from fastapi import UploadFile, status
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError

from app.config import images_dir
from app.endpoint_events import require_event
from app.pdf_files import Details, FilePath, delete_file, event_directory, get_file, list_files

router = APIRouter(prefix="/images", tags=["images"])

MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_FORMATS = {
    "PNG": (".png", "image/png"),
    "JPEG": (".jpg", "image/jpeg"),
    "GIF": (".gif", "image/gif"),
    "WEBP": (".webp", "image/webp"),
    "BMP": (".bmp", "image/bmp"),
    "TIFF": (".tiff", "image/tiff"),
}
IMAGE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]*\.(png|jpg|gif|webp|bmp|tiff)$"
ImageFilename = Annotated[str, PathParam(pattern=IMAGE_PATTERN)]
ExistingEvent = Annotated[str, Depends(require_event)]


def validate_image(path: Path) -> str:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                image_format = image.format
                if image_format not in IMAGE_FORMATS:
                    raise HTTPException(status_code=415, detail="Image format not supported")
                image.verify()
            with Image.open(path) as image:
                image.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise HTTPException(status_code=400, detail="Image dimensions are too large") from error
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        raise HTTPException(status_code=400, detail="Invalid image") from error
    return image_format


@router.post(
    "/{event_code}",
    response_model=FilePath,
    status_code=status.HTTP_201_CREATED,
    responses={code: {"model": Details} for code in (400, 404, 413, 415)},
)
def upload_image(event_code: ExistingEvent, image: Annotated[UploadFile, File()]) -> FilePath:
    directory = event_directory(images_dir(), event_code)
    directory.mkdir(parents=True, exist_ok=True)
    stem = uuid.uuid4().hex
    temporary = directory / f".{stem}.tmp"
    try:
        size = 0
        with temporary.open("xb") as output:
            while chunk := image.file.read(64 * 1024):
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    raise HTTPException(status_code=413, detail="Image exceeds 10 MiB")
                output.write(chunk)
        image_format = validate_image(temporary)
        filename = stem + IMAGE_FORMATS[image_format][0]
        os.replace(temporary, directory / filename)
    finally:
        temporary.unlink(missing_ok=True)
    return FilePath(filename=filename)


@router.get("/{event_code}", response_model=list[FilePath], responses={404: {"model": Details}})
def list_images(event_code: ExistingEvent) -> list[FilePath]:
    return [
        FilePath(filename=path.name) for path in list_files(event_directory(images_dir(), event_code), IMAGE_PATTERN)
    ]


@router.get(
    "/{event_code}/{filename}",
    response_class=FileResponse,
    responses={
        200: {"content": {media_type: {} for _, media_type in IMAGE_FORMATS.values()}},
        404: {"model": Details},
    },
)
def get_image(event_code: ExistingEvent, filename: ImageFilename) -> FileResponse:
    media_type = next(media for extension, media in IMAGE_FORMATS.values() if filename.endswith(extension))
    return get_file(event_directory(images_dir(), event_code), filename, "Image not found", media_type)


@router.delete(
    "/{event_code}/{filename}",
    response_model=FilePath,
    responses={404: {"model": Details}},
)
def delete_image(event_code: ExistingEvent, filename: ImageFilename) -> FilePath:
    return delete_file(event_directory(images_dir(), event_code), filename, "Image not found")
