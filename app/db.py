from collections.abc import AsyncIterator

from fastapi import Request
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.config import database_url

INITIAL_PRINTER_CODES = (
    ("ALGCSOIO9O_A", "Virtual printer used to generate labels"),
    ("1OPYKBGXVN_1", "1st generation black flight case"),
    ("YF1OF5FWZ0_2", "1st generation black flight case"),
    ("EK4M52IXL5_3", "1st generation black flight case"),
    ("C0ZE5TM5V4_4", "1st generation black flight case"),
    ("21YG0T6L8O_5", "1st generation black flight case"),
    ("GRR4QKRYZP_6", "1st generation black flight case"),
    ("R6CYBCY2XF_7", "1st generation black flight case"),
    ("8SCNWZUF9M_8", "1st generation black flight case"),
    ("AQ3PB4Q7I0_9", "1st generation black flight case"),
    ("COIC1PEJ8J_10", "1st generation black flight case"),
    ("S29DO41FE5_11", "1st generation black flight case"),
    ("MXSI42S9W0_12", "1st generation black flight case"),
)

INITIAL_LABEL_TYPES = (
    ("4786103", "Foldable name tag 86x103 mm", 86, 103, True),
    ("4760100", "Name tag 60x100 mm", 60, 100, False),
)


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
        await connection.execute("""
            ALTER TABLE event
                ADD COLUMN IF NOT EXISTS street TEXT CHECK (length(trim(street)) > 0),
                ADD COLUMN IF NOT EXISTS zip TEXT CHECK (length(trim(zip)) > 0),
                ADD COLUMN IF NOT EXISTS town TEXT CHECK (length(trim(town)) > 0),
                ADD COLUMN IF NOT EXISTS country TEXT CHECK (length(trim(country)) > 0)
        """)
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS printer (
                code TEXT PRIMARY KEY CHECK (code ~ '^[A-Za-z0-9]+_[A-Za-z0-9]+$'),
                label TEXT NOT NULL CHECK (label = split_part(code, '_', 2))
            )
        """)
        await connection.execute("""
            ALTER TABLE printer ADD COLUMN IF NOT EXISTS description TEXT
                CHECK (length(trim(description)) > 0)
        """)
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS label_type (
                code TEXT PRIMARY KEY CHECK (code ~ '^[0-9]+$'),
                description TEXT NOT NULL CHECK (length(trim(description)) > 0),
                height DOUBLE PRECISION NOT NULL
                    CHECK (height > 0 AND height < 'Infinity'::double precision),
                width DOUBLE PRECISION NOT NULL
                    CHECK (width > 0 AND width < 'Infinity'::double precision),
                mirrored BOOLEAN NOT NULL
            )
        """)
        async with connection.cursor() as cursor:
            await cursor.executemany(
                """
                INSERT INTO printer (code, label, description) VALUES (%s, %s, %s)
                ON CONFLICT (code) DO UPDATE
                SET description = EXCLUDED.description
                WHERE printer.description IS NULL
                """,
                [
                    (code, code.rsplit("_", 1)[1], description)
                    for code, description in INITIAL_PRINTER_CODES
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO label_type (code, description, height, width, mirrored)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (code) DO NOTHING
                """,
                INITIAL_LABEL_TYPES,
            )


async def get_connection(request: Request) -> AsyncIterator[AsyncConnection]:
    async with request.app.state.pool.connection() as connection:
        yield connection
