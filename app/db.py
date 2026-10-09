from collections.abc import AsyncIterator

from fastapi import Request
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.config import database_url


def create_pool() -> AsyncConnectionPool:
    return AsyncConnectionPool(database_url(), open=False)


async def get_connection(request: Request) -> AsyncIterator[AsyncConnection]:
    async with request.app.state.pool.connection() as connection:
        yield connection
