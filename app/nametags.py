import asyncio
import os
import re
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Path as PathParam
from fastapi import status
from fastapi.responses import FileResponse
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import BaseModel

from app import nametag_pdf
from app.codes import CODE_PATTERN
from app.config import nametags_dir
from app.db import get_connection
from app.nametag_pdf import NameData

router = APIRouter(prefix="/nametags", tags=["nametags"])

FILENAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]*\.pdf$"


class FilePath(BaseModel):
    filename: str


class Details(BaseModel):
    detail: str


Connection = Annotated[AsyncConnection, Depends(get_connection)]
BookingCode = Annotated[str, PathParam(pattern=CODE_PATTERN)]
Filename = Annotated[str, PathParam(pattern=FILENAME_PATTERN)]


def booking_dir(booking_code: str) -> Path:
    return nametags_dir() / booking_code


def list_files(directory: Path) -> list[Path]:
    try:
        entries = list(os.scandir(directory))
    except FileNotFoundError:
        return []
    files = [
        (entry.stat().st_mtime_ns, entry.name, Path(entry.path))
        for entry in entries
        if entry.is_file(follow_symlinks=False) and re.fullmatch(FILENAME_PATTERN, entry.name)
    ]
    return [path for *_, path in sorted(files)]


def write_nametag(directory: Path, filename: str, nametag_type: str, name_data: NameData) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    # Render to a hidden temporary file so listings never show a partial PDF.
    temporary = directory / f".{filename}.tmp"
    try:
        nametag_pdf.render_nametag(temporary, nametag_type, name_data)
        os.replace(temporary, directory / filename)
    finally:
        temporary.unlink(missing_ok=True)


@router.post(
    "/{booking_code}",
    response_model=FilePath,
    status_code=status.HTTP_201_CREATED,
    responses={code: {"model": Details} for code in (400, 404, 501)},
)
async def create_nametag(booking_code: BookingCode, name_data: NameData, connection: Connection) -> FilePath:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            """
            SELECT nametag_type, start_date <= CURRENT_DATE AND CURRENT_DATE <= end_date AS active
            FROM booking WHERE booking_code = %s
            """,
            (booking_code,),
        )
        booking = await cursor.fetchone()
        if booking is None:
            raise HTTPException(status_code=404, detail="Booking not found")
        if not booking["active"]:
            raise HTTPException(status_code=400, detail="Booking not valid today")
        await cursor.execute(
            "SELECT 1 FROM nametag_type_layout WHERE nametag_type_code = %s AND layout_name = %s",
            (booking["nametag_type"], name_data.layout),
        )
        if await cursor.fetchone() is None:
            raise HTTPException(status_code=400, detail="Name tag layout not supported")
    filename = f"{booking['nametag_type']}_{name_data.layout}_{uuid.uuid4().hex}.pdf"
    try:
        await asyncio.to_thread(
            write_nametag, booking_dir(booking_code), filename, booking["nametag_type"], name_data,
        )
    except NotImplementedError as error:
        raise HTTPException(status_code=501, detail="Nametag generation not implemented") from error
    return FilePath(filename=filename)


@router.get("/{booking_code}", response_model=list[FilePath])
def list_nametags(booking_code: BookingCode) -> list[FilePath]:
    return [FilePath(filename=path.name) for path in list_files(booking_dir(booking_code))]


@router.delete("/{booking_code}", response_model=list[FilePath])
def delete_nametags(booking_code: BookingCode) -> list[FilePath]:
    directory = booking_dir(booking_code)
    deleted = []
    for path in list_files(directory):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        deleted.append(FilePath(filename=path.name))
    try:
        directory.rmdir()
    except OSError:
        pass
    return deleted


@router.get(
    "/{booking_code}/{filename}",
    response_class=FileResponse,
    responses={200: {"content": {"application/pdf": {}}}, 404: {"model": Details}},
)
def get_nametag(booking_code: BookingCode, filename: Filename) -> FileResponse:
    path = booking_dir(booking_code) / filename
    if not path.is_file() or path.is_symlink():
        raise HTTPException(status_code=404, detail="Nametag not found")
    return FileResponse(path, media_type="application/pdf", filename=filename)


@router.delete("/{booking_code}/{filename}", response_model=FilePath, responses={404: {"model": Details}})
def delete_nametag(booking_code: BookingCode, filename: Filename) -> FilePath:
    path = booking_dir(booking_code) / filename
    if path.is_symlink() or not path.is_file():
        raise HTTPException(status_code=404, detail="Nametag not found")
    try:
        path.unlink()
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Nametag not found") from error
    return FilePath(filename=filename)
