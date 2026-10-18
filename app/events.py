from datetime import date
from typing import Annotated, Self

from fastapi import APIRouter, Depends, HTTPException, Path, status
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.codes import CODE_PATTERN, generate_code
from app.db import get_connection

router = APIRouter(prefix="/events", tags=["events"])


class EventCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=1)
    start_date: date
    end_date: date
    street: str | None = Field(default=None, min_length=1)
    zip: str | None = Field(default=None, min_length=1)
    town: str | None = Field(default=None, min_length=1)
    country: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class Event(EventCreate):
    id: int
    code: str


Connection = Annotated[AsyncConnection, Depends(get_connection)]
EventCode = Annotated[str, Path(pattern=CODE_PATTERN)]

EVENT_COLUMNS = "id, code, name, start_date, end_date, street, zip, town, country"


@router.post("/", response_model=Event, status_code=status.HTTP_201_CREATED)
async def create_event(event: EventCreate, connection: Connection) -> Event:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            f"""
            INSERT INTO event (code, name, start_date, end_date, street, zip, town, country)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING {EVENT_COLUMNS}
            """,
            (
                generate_code(),
                event.name, event.start_date, event.end_date,
                event.street, event.zip, event.town, event.country,
            ),
        )
        row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("Event insert did not return a row")
    return Event.model_validate(row)


@router.get("/", response_model=list[Event])
async def list_events(connection: Connection) -> list[Event]:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            f"SELECT {EVENT_COLUMNS} FROM event ORDER BY id"
        )
        rows = await cursor.fetchall()
    return [Event.model_validate(row) for row in rows]


@router.get("/{event_code}", response_model=Event)
async def get_event(event_code: EventCode, connection: Connection) -> Event:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(f"SELECT {EVENT_COLUMNS} FROM event WHERE code = %s", (event_code,))
        row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return Event.model_validate(row)


@router.put("/{event_code}", response_model=Event)
async def update_event(event_code: EventCode, event: EventCreate, connection: Connection) -> Event:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            f"""
            UPDATE event
            SET name = %s, start_date = %s, end_date = %s, street = %s, zip = %s, town = %s, country = %s
            WHERE code = %s
            RETURNING {EVENT_COLUMNS}
            """,
            (
                event.name, event.start_date, event.end_date,
                event.street, event.zip, event.town, event.country,
                event_code,
            ),
        )
        row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return Event.model_validate(row)


@router.delete("/{event_code}", response_model=Event)
async def delete_event(event_code: EventCode, connection: Connection) -> Event:
    async with connection.cursor(row_factory=dict_row) as cursor:
        # Waits for in-flight booking writes, which hold a key-share lock on the event.
        await cursor.execute("SELECT 1 FROM event WHERE code = %s FOR UPDATE", (event_code,))
        if await cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Event not found")
        await cursor.execute("DELETE FROM booking WHERE event_code = %s", (event_code,))
        await cursor.execute(f"DELETE FROM event WHERE code = %s RETURNING {EVENT_COLUMNS}", (event_code,))
        row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("Event delete did not return a row")
    return Event.model_validate(row)
