from contextlib import asynccontextmanager
from math import isfinite
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg import AsyncConnection

from app.bookings import router as bookings_router
from app.db import create_pool, get_connection, initialize_schema
from app.events import router as events_router
from app.layouts import router as layouts_router
from app.nametag_types import router as nametag_types_router
from app.nametags import router as nametags_router
from app.printers import router as printers_router

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


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    request: Request, exc: RequestValidationError,
) -> JSONResponse:
    # Nonfinite inputs must remain reportable without emitting invalid JSON.
    detail = jsonable_encoder(
        exc.errors(),
        custom_encoder={float: lambda value: value if isfinite(value) else str(value)},
    )
    return JSONResponse(status_code=422, content={"detail": detail})


@api.get("/health")
async def health(connection: Annotated[AsyncConnection, Depends(get_connection)]) -> dict:
    cursor = await connection.execute("SELECT version()")
    (version,) = await cursor.fetchone()
    return {"status": "ok", "database": version}


app.include_router(api)
app.include_router(events_router, prefix=API_PREFIX)
app.include_router(printers_router, prefix=API_PREFIX)
app.include_router(nametag_types_router, prefix=API_PREFIX)
app.include_router(layouts_router, prefix=API_PREFIX)
app.include_router(bookings_router, prefix=API_PREFIX)
app.include_router(nametags_router, prefix=API_PREFIX)
