# onsite-print-backend

The onsite-print REST API, built with FastAPI and served by uvicorn on port 8000.
It connects to the PostgreSQL database from `../onsite-print-database`.

All routes live under `/api/v2`, so the same URLs work directly and through the
nginx web server, which forwards `/api/` to `backend:8000`:

- `GET /api/v2/health` – checks the database connection
- `PUT /api/v2/event/` – creates an event and returns it with its generated ID (201)
- `GET /api/v2/event/` – returns all events, ordered by ID (an empty database returns `[]`)
- `/api/v2/docs` – interactive OpenAPI documentation
- `/api/v2/openapi.json` – OpenAPI schema

## Events

```sh
curl -X PUT http://localhost:8080/api/v2/event/ \
  -H 'Content-Type: application/json' \
  -d '{"name":"Print conference","start_date":"2026-10-09","end_date":"2026-10-10"}'

curl http://localhost:8080/api/v2/event/
```

Dates are calendar dates (`YYYY-MM-DD`), without times or timezones. All three
fields are required. Names are trimmed and cannot be blank. The end date must
be on or after the start date; same-day events are allowed. Invalid requests
return 422 and do not insert a row. Unknown fields are rejected.

As requested, PUT creates a new row on each call; repeating it creates another
event rather than updating an existing one. It is not an idempotent update.

On startup the backend creates the initial `event` table if missing, including
database constraints for required fields, nonblank names, and date ordering.
This also works with an existing PostgreSQL volume; no data reset is needed.
Schema initialization errors fail application startup rather than being ignored.
Future changes to an existing table will need migrations: `CREATE TABLE IF NOT
EXISTS` does not upgrade an existing schema.

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

For full-stack development, use
`docker compose -f compose.yaml -f compose.dev.yaml up -d --build` from the
server repo instead. It mounts `app/` into the backend and enables uvicorn reload,
alongside hot reload for both React apps. Rebuild after changing dependencies.

## Develop

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Unit tests use fake connections. To also run the PostgreSQL integration test,
set `TEST_DATABASE_CONNINFO` to an **isolated test database** using psycopg
connection parameters. That test creates events and intentionally leaves them
in that test database to verify persistence across app restarts.

## Publish a version

Publishing requires a `major.minor.patch` tag without leading zeros. The script
publishes the current version, then increments the patch in `.env` only after
the push succeeds (`0.0.1` -> `0.0.2`). Failures leave the version unchanged.
Commit the updated `.env`; its new version is the next release, not yet published.
Use the printed published tag for deployment. Do not publish concurrently or
reuse stale version files: automatic increments do not prevent registry tag
overwrites from stale checkouts or interrupted-push retries.

Set the version in `.env` (`IMAGE_TAG=...`) and `CR_PAT` in your environment, then:

```sh
bash docker-push.sh
bash tests/docker-push-test.sh   # tests the script with a mocked Docker CLI
```

GitHub Actions runs the publishing-script tests on pushes and pull requests
against this repo's own script. They do not build or upload images and require
no credentials or sibling repositories.
