import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import HTTPException
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from pydantic import BaseModel

FILENAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]*\.pdf$"
Filename = Annotated[str, PathParam(pattern=FILENAME_PATTERN)]


class FilePath(BaseModel):
    filename: str


class Details(BaseModel):
    detail: str


def event_directory(root: Path, event_code: str) -> Path:
    directory = root / event_code
    if directory.is_symlink():
        raise HTTPException(status_code=404, detail="Event files not found")
    return directory


def list_files(directory: Path, pattern: str = FILENAME_PATTERN) -> list[Path]:
    try:
        entries = list(os.scandir(directory))
    except FileNotFoundError:
        return []
    files = [
        (entry.stat().st_mtime_ns, entry.name, Path(entry.path))
        for entry in entries
        if entry.is_file(follow_symlinks=False) and re.fullmatch(pattern, entry.name)
    ]
    return [path for *_, path in sorted(files)]


def write_pdf(directory: Path, filename: str, render: Callable[[Path], None]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    # Publish only complete PDFs; failed renders must never enter the file queue.
    temporary = directory / f".{filename}.tmp"
    try:
        render(temporary)
        os.replace(temporary, directory / filename)
    finally:
        temporary.unlink(missing_ok=True)


def get_pdf(directory: Path, filename: str, missing_detail: str) -> FileResponse:
    return get_file(directory, filename, missing_detail, "application/pdf")


def get_file(directory: Path, filename: str, missing_detail: str, media_type: str) -> FileResponse:
    path = directory / filename
    if not path.is_file() or path.is_symlink():
        raise HTTPException(status_code=404, detail=missing_detail)
    return FileResponse(path, media_type=media_type, filename=filename)


def delete_pdf(directory: Path, filename: str, missing_detail: str) -> FilePath:
    return delete_file(directory, filename, missing_detail)


def delete_file(directory: Path, filename: str, missing_detail: str) -> FilePath:
    path = directory / filename
    if path.is_symlink() or not path.is_file():
        raise HTTPException(status_code=404, detail=missing_detail)
    try:
        path.unlink()
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=missing_detail) from error
    return FilePath(filename=filename)


def delete_files(directory: Path) -> list[FilePath]:
    deleted = []
    for path in list_files(directory):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        deleted.append(FilePath(filename=path.name))
    return deleted
