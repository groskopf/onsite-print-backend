from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from app.db import get_connection

router = APIRouter(prefix="/printers", tags=["printers"])


class Printer(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    code: str = Field(pattern=r"^[A-Za-z0-9]+_[A-Za-z0-9]+$")
    label: str
    description: str | None = Field(min_length=1)


class PrinterCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    code: str = Field(pattern=r"^[A-Za-z0-9]+_[A-Za-z0-9]+$")
    description: str = Field(min_length=1)


class PrinterUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    description: str = Field(min_length=1)


Connection = Annotated[AsyncConnection, Depends(get_connection)]
PrinterCode = Annotated[str, Path(pattern=r"^[A-Za-z0-9]+_[A-Za-z0-9]+$")]


@router.post("/", response_model=Printer, status_code=status.HTTP_201_CREATED)
async def create_printer(printer: PrinterCreate, connection: Connection) -> Printer:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            """
            INSERT INTO printer (code, label, description) VALUES (%s, %s, %s)
            ON CONFLICT (code) DO NOTHING
            RETURNING code, label, description
            """,
            (printer.code, printer.code.rsplit("_", 1)[1], printer.description),
        )
        row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=409, detail="Printer code already exists")
    return Printer.model_validate(row)


@router.get("/", response_model=list[Printer])
async def list_printers(connection: Connection) -> list[Printer]:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute("SELECT code, label, description FROM printer ORDER BY code")
        rows = await cursor.fetchall()
    return [Printer.model_validate(row) for row in rows]


@router.get("/{code}", response_model=Printer)
async def get_printer(code: PrinterCode, connection: Connection) -> Printer:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            "SELECT code, label, description FROM printer WHERE code = %s", (code,),
        )
        row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Printer not found")
    return Printer.model_validate(row)


@router.put("/{code}", response_model=Printer)
async def update_printer(code: PrinterCode, printer: PrinterUpdate, connection: Connection) -> Printer:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            "UPDATE printer SET description = %s WHERE code = %s RETURNING code, label, description",
            (printer.description, code),
        )
        row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Printer not found")
    return Printer.model_validate(row)


@router.delete("/{code}", response_model=Printer)
async def delete_printer(code: PrinterCode, connection: Connection) -> Printer:
    async with connection.cursor(row_factory=dict_row) as cursor:
        # Booking writes lock the same printer row, so no booking can be added in between.
        await cursor.execute("SELECT 1 FROM printer WHERE code = %s FOR UPDATE", (code,))
        if await cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Printer not found")
        await cursor.execute("SELECT 1 FROM booking WHERE printer_code = %s LIMIT 1", (code,))
        if await cursor.fetchone() is not None:
            raise HTTPException(status_code=409, detail="Printer has bookings")
        await cursor.execute(
            "DELETE FROM printer WHERE code = %s RETURNING code, label, description", (code,),
        )
        row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("Printer delete did not return a row")
    return Printer.model_validate(row)
