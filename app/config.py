import os
from pathlib import Path


def database_url() -> str:
    password = Path(os.environ.get("DB_PASSWORD_FILE", "/run/secrets/postgres_password")).read_text().strip()
    return (
        f"host={os.environ.get('DB_HOST', 'postgres')} "
        f"port={os.environ.get('DB_PORT', '5432')} "
        f"dbname={os.environ.get('DB_NAME', 'onsite_print')} "
        f"user={os.environ.get('DB_USER', 'onsite_print')} "
        f"password={password}"
    )


def nametags_dir() -> Path:
    return Path(os.environ.get("NAMETAGS_DIR", "/var/lib/onsite-print/nametags"))


def images_dir() -> Path:
    return Path(os.environ.get("IMAGES_DIR", "/var/lib/onsite-print/images"))


def sheets_dir() -> Path:
    return Path(os.environ.get("SHEETS_DIR", "/var/lib/onsite-print/sheets"))
