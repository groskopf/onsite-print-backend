from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.db import get_connection

router = APIRouter(prefix="/label_type", tags=["label types"])


class LabelType(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    code: str = Field(pattern=r"^[0-9]+$")
    description: str = Field(min_length=1)
    height: float = Field(
        gt=0, allow_inf_nan=False, strict=True, description="One-face height in millimetres",
    )
    width: float = Field(
        gt=0, allow_inf_nan=False, strict=True, description="One-face width in millimetres",
    )
    mirrored: StrictBool = Field(
        description="Repeat the face upside down for folding, not a horizontal reflection",
    )


Connection = Annotated[AsyncConnection, Depends(get_connection)]


@router.put("/", response_model=LabelType, status_code=status.HTTP_201_CREATED)
async def create_label_type(label_type: LabelType, connection: Connection) -> LabelType:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            """
            INSERT INTO label_type (code, description, height, width, mirrored)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (code) DO NOTHING
            RETURNING code, description, height, width, mirrored
            """,
            (
                label_type.code, label_type.description,
                label_type.height, label_type.width, label_type.mirrored,
            ),
        )
        row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=409, detail="Label type code already exists")
    return LabelType.model_validate(row)


@router.get("/", response_model=list[LabelType])
async def list_label_types(connection: Connection) -> list[LabelType]:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            "SELECT code, description, height, width, mirrored FROM label_type ORDER BY code",
        )
        rows = await cursor.fetchall()
    return [LabelType.model_validate(row) for row in rows]


@router.get("/{code}", response_model=LabelType)
async def get_label_type(
    code: Annotated[str, Path(pattern=r"^[0-9]+$")],
    connection: Connection,
) -> LabelType:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            "SELECT code, description, height, width, mirrored FROM label_type WHERE code = %s",
            (code,),
        )
        row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Label type not found")
    return LabelType.model_validate(row)
