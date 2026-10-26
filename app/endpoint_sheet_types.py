from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.db import get_connection

router = APIRouter(prefix="/sheet_types", tags=["sheet types"])


class SheetType(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    code: str = Field(pattern=r"^[0-9]+$")
    description: str = Field(min_length=1)
    height: float = Field(gt=0, allow_inf_nan=False, strict=True, description="Label height in millimetres")
    width: float = Field(gt=0, allow_inf_nan=False, strict=True, description="Label width in millimetres")
    folded: StrictBool


Connection = Annotated[AsyncConnection, Depends(get_connection)]
SheetCode = Annotated[str, Path(pattern=r"^[0-9]+$")]


@router.get("/", response_model=list[SheetType])
async def list_sheet_types(connection: Connection) -> list[SheetType]:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute("SELECT code, description, height, width, folded FROM sheet_type ORDER BY code")
        rows = await cursor.fetchall()
    return [SheetType.model_validate(row) for row in rows]


@router.get("/{code}", response_model=SheetType)
async def get_sheet_type(code: SheetCode, connection: Connection) -> SheetType:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            "SELECT code, description, height, width, folded FROM sheet_type WHERE code = %s",
            (code,),
        )
        row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Sheet type not found")
    return SheetType.model_validate(row)


@router.get("/{code}/layouts", response_model=list[str])
async def get_sheet_type_layouts(code: SheetCode, connection: Connection) -> list[str]:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            """
            SELECT ARRAY(
                SELECT layout.name
                FROM sheet_type_layout AS supported
                JOIN layout ON layout.name = supported.layout_name
                WHERE supported.sheet_type_code = sheet_type.code
                ORDER BY layout.position, layout.name
            ) AS layouts
            FROM sheet_type WHERE code = %s
            """,
            (code,),
        )
        row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Sheet type not found")
    return row["layouts"]
