# onsite-print-backend

The onsite-print REST API, built with FastAPI and served by uvicorn on port 8000.
It connects to the PostgreSQL database from `../onsite-print-database`.

All routes live under `/api`, so the same URLs work directly and through the
nginx web server, which forwards `/api/` to `backend:8000`:

- `GET /api/health` – checks the database connection
- `/api/docs` – interactive OpenAPI documentation

## Configuration

| Variable           | Default                          |
|--------------------|----------------------------------|
| `DB_HOST`          | `postgres`                       |
| `DB_PORT`          | `5432`                           |
| `DB_NAME`          | `onsite_print`                   |
| `DB_USER`          | `onsite_print`                   |
| `DB_PASSWORD_FILE` | `/run/secrets/postgres_password` |

In Compose the password is the `postgres_password` secret, read from
`../onsite-print-database/secrets/postgres_password`.

## Run

Normally the whole stack is started from `../onsite-print-server`. To run just the
database and the backend, start the database first, then:

```sh
docker compose up -d --build
```

## Develop

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

## Publish a version

Set the version in `.env` (`IMAGE_TAG=...`) and `CR_PAT` in your environment, then:

```sh
bash docker-push.sh
bash tests/docker-push-test.sh   # tests the script with a mocked Docker CLI
```
