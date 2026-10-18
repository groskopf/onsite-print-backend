from collections.abc import AsyncIterator

from fastapi import Request
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.codes import generate_code
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

INITIAL_NAMETAG_TYPES = (
    ("4786103", "Foldable name tag 86x103 mm", 86, 103, True),
    ("4760100", "Name tag 60x100 mm", 60, 100, False),
)

INITIAL_LAYOUTS = (
    ("layout_1", "En linie"),
    ("layout_2", "To liner"),
    ("layout_2PT", "To lininer billede i toppen"),
    ("layout_2PB", "To lininer billede i bunden"),
    ("layout_2PTL", "To lininer billede i toppen til venstre"),
    ("layout_2PTR", "To lininer billede i toppen til højre"),
    ("layout_2PBL", "To lininer billede i bunden til venstre"),
    ("layout_2PBR", "To lininer billede i bunden til højre"),
    ("layout_2QT", "To lininer QR code i toppen"),
    ("layout_2QB", "To lininer QR code i bunden"),
    ("layout_2QTL", "To lininer QR i toppen til venstre"),
    ("layout_2QTR", "To lininer QR i toppen til højre"),
    ("layout_2QBL", "To lininer QR i bunden til venstre"),
    ("layout_2QBR", "To lininer QR i bunden til højre"),
    ("layout_2PTLQ", "To lininer billede i toppen til venstre med QR"),
    ("layout_2PTRQ", "To lininer billede i toppen til højre med QR"),
    ("layout_2PBLQ", "To lininer billede i bunden til venstre med QR"),
    ("layout_2PBRQ", "To lininer billede i bunden til højre med QR"),
    ("layout_3", "Tre linier"),
    ("layout_3PT", "Tre linier med billede i toppen"),
    ("layout_3PB", "Tre linier med billede i bunden"),
    ("layout_3PTL", "Tre linier med billede i toppen til venstre"),
    ("layout_3PTR", "Tre linier med billede i toppen til højre"),
    ("layout_3PBL", "Tre linier med billede i bunden til venstre"),
    ("layout_3PBR", "Tre linier med billede i bunden til højre"),
    ("layout_3PTLQ", "Tre linier med billede i toppen til venstre med QR"),
    ("layout_3PTRQ", "Tre linier med billede i toppen til højre med QR"),
    ("layout_3PBLQ", "Tre linier med billede i bunden til venstre med QR"),
    ("layout_3PBRQ", "Tre linier med billede i bunden til højre med QR"),
    ("layout_3QT", "Tre linier med QR i toppen"),
    ("layout_3QB", "Tre linier med QR i bunden"),
    ("layout_3QTL", "Tre linier med QR i toppen til venstre"),
    ("layout_3QTR", "Tre linier med QR i toppen til højre"),
    ("layout_3QBL", "Tre linier med QR i bunden til venstre"),
    ("layout_3QBR", "Tre linier med QR i bunden til højre"),
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
            DO $$
            BEGIN
                IF to_regclass('label_type') IS NOT NULL THEN
                    IF to_regclass('nametag_type') IS NOT NULL THEN
                        RAISE EXCEPTION 'Both label_type and nametag_type exist; resolve before startup';
                    END IF;
                    ALTER TABLE label_type RENAME TO nametag_type;
                END IF;
            END
            $$
        """)
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS nametag_type (
                code TEXT PRIMARY KEY CHECK (code ~ '^[0-9]+$'),
                description TEXT NOT NULL CHECK (length(trim(description)) > 0),
                height DOUBLE PRECISION NOT NULL
                    CHECK (height > 0 AND height < 'Infinity'::double precision),
                width DOUBLE PRECISION NOT NULL
                    CHECK (width > 0 AND width < 'Infinity'::double precision),
                folded BOOLEAN NOT NULL
            )
        """)
        await connection.execute("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_attribute
                    WHERE attrelid = 'nametag_type'::regclass
                      AND attname = 'mirrored' AND NOT attisdropped
                ) THEN
                    ALTER TABLE nametag_type RENAME COLUMN mirrored TO folded;
                END IF;
            END
            $$
        """)
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS layout (
                name TEXT PRIMARY KEY CHECK (length(trim(name)) > 0),
                position INTEGER NOT NULL CHECK (position >= 0)
            )
        """)
        await connection.execute("""
            ALTER TABLE layout ADD COLUMN IF NOT EXISTS description TEXT
                CHECK (length(trim(description)) > 0)
        """)
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS nametag_type_layout (
                nametag_type_code TEXT NOT NULL REFERENCES nametag_type(code),
                layout_name TEXT NOT NULL REFERENCES layout(name),
                PRIMARY KEY (nametag_type_code, layout_name)
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
                INSERT INTO nametag_type (code, description, height, width, folded)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (code) DO NOTHING
                """,
                INITIAL_NAMETAG_TYPES,
            )
            await cursor.executemany(
                """
                INSERT INTO layout (name, position, description) VALUES (%s, %s, %s)
                ON CONFLICT (name) DO UPDATE
                SET description = EXCLUDED.description
                WHERE layout.description IS NULL
                """,
                [
                    (name, position, description)
                    for position, (name, description) in enumerate(INITIAL_LAYOUTS)
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO nametag_type_layout (nametag_type_code, layout_name)
                VALUES (%s, %s)
                ON CONFLICT (nametag_type_code, layout_name) DO NOTHING
                """,
                [
                    (code, name)
                    for code, *_ in INITIAL_NAMETAG_TYPES
                    for name, _ in INITIAL_LAYOUTS
                ],
            )


async def get_connection(request: Request) -> AsyncIterator[AsyncConnection]:
    async with request.app.state.pool.connection() as connection:
        yield connection
