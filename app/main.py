from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI
from psycopg import AsyncConnection

from app.db import create_pool, get_connection


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = create_pool()
    await app.state.pool.open()
    try:
        yield
    finally:
        await app.state.pool.close()


app = FastAPI(
    title="onsite-print API",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)
api = APIRouter(prefix="/api")


@api.get("/health")
async def health(connection: Annotated[AsyncConnection, Depends(get_connection)]) -> dict:
    cursor = await connection.execute("SELECT version()")
    (version,) = await cursor.fetchone()
    return {"status": "ok", "database": version}


app.include_router(api)
