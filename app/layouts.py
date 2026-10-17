from typing import Annotated

from fastapi import APIRouter, Depends
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.db import get_connection

router = APIRouter(prefix="/layout", tags=["layouts"])


@router.get("/", response_model=list[str])
async def list_layouts(
    connection: Annotated[AsyncConnection, Depends(get_connection)],
) -> list[str]:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute("SELECT name FROM layout ORDER BY position, name")
        rows = await cursor.fetchall()
    return [row["name"] for row in rows]
