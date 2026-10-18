from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.db import get_connection

router = APIRouter(prefix="/nametag_type", tags=["nametag types"])


class NametagType(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    code: str = Field(pattern=r"^[0-9]+$")
    description: str = Field(min_length=1)
    height: float = Field(
        gt=0,
        allow_inf_nan=False,
        strict=True,
        description="One-face height in millimetres",
    )
    width: float = Field(
        gt=0,
        allow_inf_nan=False,
        strict=True,
        description="One-face width in millimetres",
    )
    folded: StrictBool = Field(
        description="Repeat the face upside down for folding, not a horizontal reflection",
    )


Connection = Annotated[AsyncConnection, Depends(get_connection)]


@router.put("/", response_model=NametagType, status_code=status.HTTP_201_CREATED)
async def create_nametag_type(nametag_type: NametagType, connection: Connection) -> NametagType:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            """
            INSERT INTO nametag_type (code, description, height, width, folded)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (code) DO NOTHING
            RETURNING code, description, height, width, folded
            """,
            (
                nametag_type.code,
                nametag_type.description,
                nametag_type.height,
                nametag_type.width,
                nametag_type.folded,
            ),
        )
        row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=409, detail="Nametag type code already exists")
    return NametagType.model_validate(row)


@router.get("/", response_model=list[NametagType])
async def list_nametag_types(connection: Connection) -> list[NametagType]:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            "SELECT code, description, height, width, folded FROM nametag_type ORDER BY code",
        )
        rows = await cursor.fetchall()
    return [NametagType.model_validate(row) for row in rows]


@router.get("/{code}", response_model=NametagType)
async def get_nametag_type(
    code: Annotated[str, Path(pattern=r"^[0-9]+$")],
    connection: Connection,
) -> NametagType:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            "SELECT code, description, height, width, folded FROM nametag_type WHERE code = %s",
            (code,),
        )
        row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Nametag type not found")
    return NametagType.model_validate(row)


@router.get("/{code}/layouts", response_model=list[str])
async def get_nametag_type_layouts(
    code: Annotated[str, Path(pattern=r"^[0-9]+$")],
    connection: Connection,
) -> list[str]:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            """
            SELECT ARRAY(
                SELECT layout.name
                FROM nametag_type_layout AS supported
                JOIN layout ON layout.name = supported.layout_name
                WHERE supported.nametag_type_code = nametag_type.code
                ORDER BY layout.position, layout.name
            ) AS layouts
            FROM nametag_type WHERE code = %s
            """,
            (code,),
        )
        row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Nametag type not found")
    return row["layouts"]
