from collections.abc import AsyncIterator

from fastapi import Request
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.config import database_url


def create_pool() -> AsyncConnectionPool:
    return AsyncConnectionPool(database_url(), open=False)


async def initialize_schema(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as connection:
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS event (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                name TEXT NOT NULL CHECK (length(trim(name)) > 0),
                start_date DATE NOT NULL,
                end_date DATE NOT NULL CHECK (end_date >= start_date)
            )
        """)


async def get_connection(request: Request) -> AsyncIterator[AsyncConnection]:
    async with request.app.state.pool.connection() as connection:
        yield connection
