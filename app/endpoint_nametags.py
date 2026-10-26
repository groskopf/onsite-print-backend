import asyncio
import errno
import uuid
from functools import partial
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
from app.endpoint_events import EventCode
from app.nametag_pdf import NameData
from app.pdf_files import (
    Details,
    Filename,
    FilePath,
    delete_files,
    delete_pdf,
    event_directory,
    get_pdf,
    list_files,
    write_pdf,
)

Connection = Annotated[AsyncConnection, Depends(get_connection)]
BookingCode = Annotated[str, PathParam(pattern=CODE_PATTERN)]


class BookingContext(BaseModel):
    nametag_type: str
    event_code: str
    active: bool


async def require_booking(event_code: EventCode, booking_code: BookingCode, connection: Connection) -> BookingContext:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            """
            SELECT b.nametag_type, b.event_code,
                   b.start_date <= CURRENT_DATE AND CURRENT_DATE <= b.end_date AS active
            FROM booking b JOIN event e ON e.code = b.event_code
            WHERE b.booking_code = %s AND b.event_code = %s
            FOR SHARE OF b, e
            """,
            (booking_code, event_code),
        )
        booking = await cursor.fetchone()
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    return BookingContext.model_validate(booking)


ExistingBooking = Annotated[BookingContext, Depends(require_booking)]
router = APIRouter(prefix="/nametags", tags=["nametags"], dependencies=[Depends(require_booking)])


def booking_dir(event_code: str, booking_code: str) -> Path:
    return event_directory(event_directory(nametags_dir(), event_code), booking_code)


def write_nametag(directory: Path, filename: str, nametag_type: str, name_data: NameData, event_code: str) -> None:
    write_pdf(
        directory,
        filename,
        partial(nametag_pdf.render_nametag, nametag_type=nametag_type, name_data=name_data, event_code=event_code),
    )


@router.post(
    "/{event_code}/{booking_code}",
    response_model=FilePath,
    status_code=status.HTTP_201_CREATED,
    responses={code: {"model": Details} for code in (400, 404)},
)
async def create_nametag(
    event_code: EventCode,
    booking_code: BookingCode,
    name_data: NameData,
    connection: Connection,
    booking: ExistingBooking,
) -> FilePath:
    if not booking.active:
        raise HTTPException(status_code=400, detail="Booking not valid today")
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            "SELECT 1 FROM nametag_type_layout WHERE nametag_type_code = %s AND layout_name = %s",
            (booking.nametag_type, name_data.layout),
        )
        if await cursor.fetchone() is None:
            raise HTTPException(status_code=400, detail="Name tag layout not supported")
    filename = f"{booking.nametag_type}_{name_data.layout}_{uuid.uuid4().hex}.pdf"
    try:
        await asyncio.to_thread(
            write_nametag,
            booking_dir(event_code, booking_code),
            filename,
            booking.nametag_type,
            name_data,
            booking.event_code,
        )
    except (nametag_pdf.ImageNotFound, nametag_pdf.UnsupportedNametagType) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except nametag_pdf.UnsupportedLayout as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return FilePath(filename=filename)


@router.get("/{event_code}/{booking_code}", response_model=list[FilePath], responses={404: {"model": Details}})
def list_nametags(event_code: EventCode, booking_code: BookingCode) -> list[FilePath]:
    return [FilePath(filename=path.name) for path in list_files(booking_dir(event_code, booking_code))]


@router.delete("/{event_code}/{booking_code}", response_model=list[FilePath], responses={404: {"model": Details}})
def delete_nametags(event_code: EventCode, booking_code: BookingCode) -> list[FilePath]:
    directory = booking_dir(event_code, booking_code)
    deleted = delete_files(directory)
    try:
        directory.rmdir()
    except FileNotFoundError:
        pass
    except OSError as error:
        if error.errno != errno.ENOTEMPTY:
            raise
    return deleted


@router.get(
    "/{event_code}/{booking_code}/{filename}",
    response_class=FileResponse,
    responses={200: {"content": {"application/pdf": {}}}, 404: {"model": Details}},
)
def get_nametag(event_code: EventCode, booking_code: BookingCode, filename: Filename) -> FileResponse:
    return get_pdf(booking_dir(event_code, booking_code), filename, "Nametag not found")


@router.delete("/{event_code}/{booking_code}/{filename}", response_model=FilePath, responses={404: {"model": Details}})
def delete_nametag(event_code: EventCode, booking_code: BookingCode, filename: Filename) -> FilePath:
    return delete_pdf(booking_dir(event_code, booking_code), filename, "Nametag not found")
