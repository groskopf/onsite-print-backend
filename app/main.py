from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI
from psycopg import AsyncConnection

from app.db import create_pool, get_connection, initialize_schema
from app.events import router as events_router

API_PREFIX = "/api/v2"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = create_pool()
    await app.state.pool.open()
    try:
        await initialize_schema(app.state.pool)
        yield
    finally:
        await app.state.pool.close()


app = FastAPI(
    title="onsite-print API",
    lifespan=lifespan,
    docs_url=f"{API_PREFIX}/docs",
    redoc_url=None,
    openapi_url=f"{API_PREFIX}/openapi.json",
)
api = APIRouter(prefix=API_PREFIX)


@api.get("/health")
async def health(connection: Annotated[AsyncConnection, Depends(get_connection)]) -> dict:
    cursor = await connection.execute("SELECT version()")
    (version,) = await cursor.fetchone()
    return {"status": "ok", "database": version}


app.include_router(api)
app.include_router(events_router, prefix=API_PREFIX)
