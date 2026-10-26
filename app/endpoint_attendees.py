from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from app.codes import ATTENDEE_CODE_PATTERN, generate_attendee_code
from app.endpoint_events import Connection, require_event

router = APIRouter(prefix="/attendees", tags=["attendees"])


class AttendeeCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    address: str | None = Field(default=None, min_length=1)
    zip: str | None = Field(default=None, min_length=1)
    city: str | None = Field(default=None, min_length=1)
    country: str | None = Field(default=None, min_length=1)
    company: str | None = Field(default=None, min_length=1)
    phone: str | None = Field(default=None, min_length=1)
    email: str | None = Field(default=None, min_length=1)


class Attendee(AttendeeCreate):
    code: str
    event_code: str


ExistingEvent = Annotated[str, Depends(require_event)]
AttendeeCode = Annotated[str, Path(pattern=ATTENDEE_CODE_PATTERN)]
ATTENDEE_COLUMNS = "code, event_code, first_name, last_name, address, zip, city, country, company, phone, email"
ATTENDEE_EXISTS = "attendee_exists"


@router.post("/{event_code}", response_model=Attendee, status_code=status.HTTP_201_CREATED)
async def create_attendee(
    event_code: ExistingEvent,
    attendee: AttendeeCreate,
    connection: Connection,
    allow_duplicate: Annotated[bool, Query()] = False,
) -> Attendee:
    async with connection.cursor(row_factory=dict_row) as cursor:
        # Serialize creates per event so concurrent duplicate checks cannot both miss each other.
        await cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"attendee:{event_code}",))
        if not allow_duplicate:
            await cursor.execute(
                f"""
                SELECT {ATTENDEE_COLUMNS} FROM attendee
                WHERE event_code = %s AND lower(first_name) = lower(%s) AND lower(last_name) = lower(%s)
                    AND lower(email) IS NOT DISTINCT FROM lower(%s)
                ORDER BY code LIMIT 1
                """,
                (event_code, attendee.first_name, attendee.last_name, attendee.email),
            )
            existing = await cursor.fetchone()
            if existing is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": ATTENDEE_EXISTS,
                        "message": "An attendee with the same name and email already exists",
                        "attendee": Attendee.model_validate(existing).model_dump(),
                    },
                )
        await cursor.execute(
            f"""
            INSERT INTO attendee ({ATTENDEE_COLUMNS})
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING {ATTENDEE_COLUMNS}
            """,
            (
                generate_attendee_code(),
                event_code,
                attendee.first_name,
                attendee.last_name,
                attendee.address,
                attendee.zip,
                attendee.city,
                attendee.country,
                attendee.company,
                attendee.phone,
                attendee.email,
            ),
        )
        row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("Attendee insert did not return a row")
    return Attendee.model_validate(row)


@router.get("/{event_code}", response_model=list[Attendee])
async def list_attendees(event_code: ExistingEvent, connection: Connection) -> list[Attendee]:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            f"SELECT {ATTENDEE_COLUMNS} FROM attendee WHERE event_code = %s ORDER BY last_name, first_name, code",
            (event_code,),
        )
        rows = await cursor.fetchall()
    return [Attendee.model_validate(row) for row in rows]


@router.get("/{event_code}/{attendee_code}", response_model=Attendee)
async def get_attendee(event_code: ExistingEvent, attendee_code: AttendeeCode, connection: Connection) -> Attendee:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            f"SELECT {ATTENDEE_COLUMNS} FROM attendee WHERE event_code = %s AND code = %s",
            (event_code, attendee_code),
        )
        row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Attendee not found")
    return Attendee.model_validate(row)


@router.put("/{event_code}/{attendee_code}", response_model=Attendee)
async def update_attendee(
    event_code: ExistingEvent, attendee_code: AttendeeCode, attendee: AttendeeCreate, connection: Connection
) -> Attendee:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            f"""
            UPDATE attendee
            SET first_name = %s, last_name = %s, address = %s, zip = %s, city = %s,
                country = %s, company = %s, phone = %s, email = %s
            WHERE event_code = %s AND code = %s
            RETURNING {ATTENDEE_COLUMNS}
            """,
            (
                attendee.first_name,
                attendee.last_name,
                attendee.address,
                attendee.zip,
                attendee.city,
                attendee.country,
                attendee.company,
                attendee.phone,
                attendee.email,
                event_code,
                attendee_code,
            ),
        )
        row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Attendee not found")
    return Attendee.model_validate(row)


@router.delete("/{event_code}/{attendee_code}", response_model=Attendee)
async def delete_attendee(event_code: ExistingEvent, attendee_code: AttendeeCode, connection: Connection) -> Attendee:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            f"DELETE FROM attendee WHERE event_code = %s AND code = %s RETURNING {ATTENDEE_COLUMNS}",
            (event_code, attendee_code),
        )
        row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Attendee not found")
    return Attendee.model_validate(row)
