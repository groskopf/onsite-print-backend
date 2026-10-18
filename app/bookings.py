from datetime import date
from typing import Annotated, Any, Self

from fastapi import APIRouter, Depends, HTTPException, Path, status
from psycopg import AsyncConnection, AsyncCursor
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.codes import CODE_PATTERN, generate_code
from app.db import get_connection

router = APIRouter(prefix="/bookings", tags=["bookings"])


class BookingCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    event_code: str = Field(pattern=CODE_PATTERN)
    start_date: date
    end_date: date
    printer_code: str = Field(pattern=r"^[A-Za-z0-9]+_[A-Za-z0-9]+$")
    nametag_type: str = Field(pattern=r"^[0-9]+$")

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class Booking(BookingCreate):
    booking_code: str


Connection = Annotated[AsyncConnection, Depends(get_connection)]
BookingCode = Annotated[str, Path(pattern=CODE_PATTERN)]

BOOKING_COLUMNS = "booking_code, event_code, printer_code, nametag_type, start_date, end_date"


@router.get("/", response_model=list[Booking])
async def list_bookings(connection: Connection) -> list[Booking]:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            f"SELECT {BOOKING_COLUMNS} FROM booking ORDER BY start_date, printer_code, booking_code",
        )
        rows = await cursor.fetchall()
    return [Booking.model_validate(row) for row in rows]


async def validate_booking(cursor: AsyncCursor[Any], booking: BookingCreate, exclude_code: str | None = None) -> None:
    # Key-share lock keeps the event from being deleted until this booking write commits.
    await cursor.execute("SELECT code FROM event WHERE code = %s FOR KEY SHARE", (booking.event_code,))
    if await cursor.fetchone() is None:
        raise HTTPException(status_code=404, detail="Event not found")
    # Locking the printer row serializes concurrent overlap checks for that printer.
    await cursor.execute("SELECT code FROM printer WHERE code = %s FOR UPDATE", (booking.printer_code,))
    if await cursor.fetchone() is None:
        raise HTTPException(status_code=404, detail="Printer not found")
    await cursor.execute("SELECT code FROM nametag_type WHERE code = %s", (booking.nametag_type,))
    if await cursor.fetchone() is None:
        raise HTTPException(status_code=404, detail="Nametag type not found")
    await cursor.execute(
        """
        SELECT 1 FROM booking
        WHERE printer_code = %s AND start_date <= %s AND %s <= end_date
          AND booking_code IS DISTINCT FROM %s
        LIMIT 1
        """,
        (booking.printer_code, booking.end_date, booking.start_date, exclude_code),
    )
    if await cursor.fetchone() is not None:
        raise HTTPException(status_code=409, detail="Printer has booking in same period")


@router.post("/", response_model=Booking, status_code=status.HTTP_201_CREATED)
async def create_booking(booking: BookingCreate, connection: Connection) -> Booking:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await validate_booking(cursor, booking)
        await cursor.execute(
            f"""
            INSERT INTO booking ({BOOKING_COLUMNS}) VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING {BOOKING_COLUMNS}
            """,
            (
                generate_code(),
                booking.event_code,
                booking.printer_code,
                booking.nametag_type,
                booking.start_date,
                booking.end_date,
            ),
        )
        row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("Booking insert did not return a row")
    return Booking.model_validate(row)


@router.get("/{booking_code}", response_model=Booking)
async def get_booking(booking_code: BookingCode, connection: Connection) -> Booking:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(f"SELECT {BOOKING_COLUMNS} FROM booking WHERE booking_code = %s", (booking_code,))
        row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    return Booking.model_validate(row)


@router.put("/{booking_code}", response_model=Booking)
async def update_booking(booking_code: BookingCode, booking: BookingCreate, connection: Connection) -> Booking:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute("SELECT 1 FROM booking WHERE booking_code = %s FOR UPDATE", (booking_code,))
        if await cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Booking not found")
        await validate_booking(cursor, booking, exclude_code=booking_code)
        await cursor.execute(
            f"""
            UPDATE booking
            SET event_code = %s, printer_code = %s, nametag_type = %s, start_date = %s, end_date = %s
            WHERE booking_code = %s
            RETURNING {BOOKING_COLUMNS}
            """,
            (
                booking.event_code,
                booking.printer_code,
                booking.nametag_type,
                booking.start_date,
                booking.end_date,
                booking_code,
            ),
        )
        row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("Booking update did not return a row")
    return Booking.model_validate(row)


@router.delete("/{booking_code}", response_model=Booking)
async def delete_booking(booking_code: BookingCode, connection: Connection) -> Booking:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            f"DELETE FROM booking WHERE booking_code = %s RETURNING {BOOKING_COLUMNS}",
            (booking_code,),
        )
        row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    return Booking.model_validate(row)
