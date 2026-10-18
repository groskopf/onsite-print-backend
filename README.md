# onsite-print-backend

The onsite-print REST API, built with FastAPI and served by uvicorn on port 8000.
It connects to the PostgreSQL database from `../onsite-print-database`.

## Documentation for developers and agents

Agents should read the [docs/](docs/) folder before working on this backend:

- [Development setup](docs/development.md): environment, tools, tests,
  troubleshooting, and known Copilot session-sharing limitations.
- [Architecture](docs/architecture.md): application structure, API conventions,
  data model, migrations, and deferred features.

These guides preserve project knowledge from local agent sessions so a new
session does not depend on access to previous chat history. Keep them updated
when changing the setup or architecture.

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

Events also accept optional `street`, `zip`, `town`, and `country` fields.
Each can be omitted or set to `null`; supplied values must be nonblank strings
and are trimmed. Zip codes remain strings, preserving leading zeros and letters.
Create and list responses include all four fields, with `null` for missing values.

```json
{
  "name": "Print conference",
  "start_date": "2026-10-09",
  "end_date": "2026-10-10",
  "street": "Main Street 12",
  "zip": "0123",
  "town": "Copenhagen",
  "country": "Denmark"
}
```

As requested, PUT creates a new row on each call; repeating it creates another
event rather than updating an existing one. It is not an idempotent update.

On startup the backend creates the initial `event` table if missing, including
database constraints for required fields, nonblank names, and date ordering.
This also works with an existing PostgreSQL volume; no data reset is needed.
Schema initialization errors fail application startup rather than being ignored.
Startup also adds the nullable address columns to existing tables using
`ADD COLUMN IF NOT EXISTS`. Existing events are preserved with `null` address
values; restarting is safe. Other future schema changes still need explicit
migrations: `CREATE TABLE IF NOT EXISTS` alone does not upgrade an existing schema.

## Printers

Printers are stored in a `printer` table keyed by their full code. Startup seeds
the 13 codes from
[printer_code.py](https://github.com/groskopf/printerbox_pdf/blob/3da0c8d91cb90f33ceae31471f431d94958080fb/printer_code.py).
Seeding is repeatable and preserves existing records and newly added printers.
The live legacy API also includes `Pey3NazUi3_B`, which is absent from that source
file and is not seeded here; it can be added through the API.

- `PUT /api/v2/printer/` accepts
  `{"code":"NEWPRINTER_13","description":"New flight case"}` and returns
  `{"code":"NEWPRINTER_13","label":"13","description":"New flight case"}` with status 201.
- `GET /api/v2/printer/{code}` returns one printer, or 404 if it does not exist.
- `GET /api/v2/printer/` returns all printers sorted by code (or `[]`).

The label is automatically derived from the suffix after `_`, including
multi-digit labels such as `"10"`. Codes are case-sensitive alphanumeric strings
with exactly one underscore separating a nonempty prefix and label. Creation
trims surrounding whitespace. Invalid codes and extra request fields return 422;
adding an existing code returns 409 without changing that record.
Labels are strings; no client-supplied label is needed.

New printers require a nonblank string `description`, trimmed on creation.
All printer responses include it. Startup adds the nullable description column
to older tables and fills missing descriptions for the seeded printers, without
overwriting existing descriptions. Legacy custom printers without descriptions
remain readable with `description: null`. The seeded virtual printer is described
as "Virtual printer used to generate labels"; the others as
"1st generation black flight case".

The legacy API exposes codes as an enum used in booking routes but has no
standalone printer CRUD endpoints. These new routes follow the current API's
singular resource and PUT-creation conventions instead.

## Nametag types

Nametag types replace the legacy
[NameTagType enum](https://github.com/groskopf/printerbox_pdf/blob/3da0c8d91cb90f33ceae31471f431d94958080fb/pdf/name_tag_type.py)
with a database-backed `nametag_type` table. Startup seeds these two types, using
the dimensions from the legacy PDF templates:

| Code | Description | Height (mm) | Width (mm) | Folded |
|------|-------------|-------------|------------|----------|
| `4760100` | Name tag 60x100 mm | 60 | 100 | false |
| `4786103` | Foldable name tag 86x103 mm | 86 | 103 | true |

Height and width describe **one face**, before padding. `folded` means an
upside-down duplicate for folding, not horizontally reflected text. Thus the
foldable type has a one-face height of 86 mm, not its unfolded height of 172 mm.
These are stored properties; PDF rendering is not implemented by these endpoints.

- `PUT /api/v2/nametag_type/` creates a type and returns all its fields (201).
- `GET /api/v2/nametag_type/{code}` returns one type, or 404 if absent.
- `GET /api/v2/nametag_type/` returns all types sorted by code (or `[]`).

Example creation body:

```json
{
  "code": "1234567",
  "description": "Custom foldable label",
  "height": 86.5,
  "width": 103,
  "folded": true
}
```

All five fields are required. Codes are case-sensitive digit-only strings
(leading zeros are preserved); codes and descriptions are trimmed and cannot
be blank. Dimensions must be finite, positive JSON numbers in millimetres,
and `folded` must be a JSON boolean. Invalid input and extra fields return 422.
Nonfinite numeric input is rejected with a JSON validation error; nonfinite
values echoed in error details are represented as strings (`"nan"`, `"inf"`,
or `"-inf"`) so the error response remains valid JSON.
Duplicate codes return 409 without updating the existing type. Startup seeding
is repeatable and never overwrites existing types, including customized seeds.
No reset of existing database volumes is needed.
Startup renames an existing `label_type` table to `nametag_type`, preserving its
records. If both tables exist, startup fails rather than choosing one silently.
The old `/api/v2/label_type/` endpoints are replaced, not retained as aliases.
Startup also renames the old `mirrored` column to `folded`, preserving its boolean
values. API requests and responses use only `folded`; `mirrored` is no longer
accepted.

The legacy API uses the enum in bookings and layout routes, but does not expose
nametag-type management endpoints. These routes therefore follow the current
singular-resource and PUT-creation conventions, keeping `/api/v2`.

## Layouts

Layouts are shared between sheets and nametags, so there is one catalogue:

- `GET /api/v2/layout/` returns layout names as a JSON array, for example
  `["layout_1", "layout_2", "layout_2PT", ...]`.
- `GET /api/v2/nametag_type/{code}/layouts` returns the supported layout names
  for that type in the same order. Unknown types return 404; an existing type
  without assignments returns `[]`. Invalid type codes return 422.

The `layout` table stores each unique name, its ordering position, and its Danish
description. Startup adds the nullable `description` column to existing tables
and fills missing descriptions for seeded layouts without overwriting saved
descriptions. Older custom layouts may retain a null description; non-null
descriptions cannot be blank. The GET endpoints still return layout names only.
`nametag_type_layout` associates types with layouts using foreign keys and
prevents duplicate assignments. Startup seeds the 35 valid names from the legacy
[Layout enum](https://github.com/groskopf/printerbox_pdf/blob/main/pdf/layouts.py),
excluding its `invalid` sentinel. Both seeded nametag types support all 35,
matching [name_tag_layouts.py](https://github.com/groskopf/printerbox_pdf/blob/main/pdf/name_tag_layouts.py).
Seeding adds missing reference entries without overwriting existing entries or
custom assignments. Newly created nametag types have no assignments by default.

These are GET-only reference endpoints, not layout editing or PDF rendering.
Unlike the legacy API's separate `/layouts/name_tags` and `/layouts/sheets`
type-grouped lists, the catalogue is shared and type-specific support is exposed
under the nametag type. No sheet-type table or sheet-specific associations are
introduced yet; the legacy sheet mappings currently allow only `layout_1`.

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

### VS Code dev container

Open this repository folder in VS Code, install the **Dev Containers** extension,
and run **Dev Containers: Reopen in Container**. The configuration in
[.devcontainer/devcontainer.json](.devcontainer/devcontainer.json) provides
the same Python 3.12 runtime and application dependencies as the production
image, plus a writable virtual environment and the development tools Black,
Flake8, isort, mypy, pre-commit, pytest/httpx, pytest-cov, and Ruff;
Python/Pylance extensions, Git, GitHub CLI, curl, OpenSSH, Docker CLI/Compose/Buildx,
PostgreSQL client tools, jq, and ripgrep.
No Node.js feature or frontend dependencies are installed.

The shared `runtime` stage in [Dockerfile](Dockerfile) installs everything needed
to run the application. The devcontainer builds its `development` stage and
installs the `dev` optional dependency group from `pyproject.toml` in editable
mode. The default final `production` stage contains no developer tools.
After changing dependencies, run `python -m pip install -e '.[dev]'` again.
Run `python -m pytest` from the container terminal. If VS Code retained a host
interpreter selection, choose `/opt/onsite-print-venv/bin/python` with
**Python: Select Interpreter**.
Run `python -m black app tests` to format, `python -m flake8 app tests` to lint,
`python -m isort app tests` to sort imports, and `python -m mypy app` for type
checking. `python -m pytest --cov=app` collects test coverage. Saving a Python
file in the devcontainer formats it with Black and organizes its imports with
isort. Black, isort, Flake8, and Ruff use a 120-character line length. To run
Flake8, mypy, and pytest from VS Code, run the
**Python: Run checks** task from the Command Palette (**Tasks: Run Test Task**).

The parent directory is mounted at its original absolute host path so sibling
repositories and Compose bind mounts remain accessible. Keep the checkout in a
dedicated parent directory: the container can access everything in that directory.
Docker uses the host engine, granting control over host containers; use trusted
code only. Setup does not start services, create secrets, or reset database data.
Port 8000 is forwarded if you start uvicorn manually.

The container shares the host's `~/.copilot/session-state` and `~/.config/gh`
directories at the same relative locations in the container user's home. The
Dev Containers extension creates both host directories before starting the
container. This makes Copilot session files and file-backed GitHub CLI
authentication available without mounting the rest of the Copilot configuration.
Both mounts are writable so updates made by Copilot or `gh` persist on the host.

If `gh auth status` works on the host but not in the container, the host is
probably storing its token in an OS keyring rather than `~/.config/gh/hosts.yml`.
Export `GH_TOKEN` in the environment used to launch VS Code, then rebuild and
reopen the container; the configuration forwards that variable. Alternatively,
run `gh auth login --insecure-storage` on the host to put the token in the shared
configuration, understanding that this stores it as plain text. Verify sharing
inside the container with `gh auth status`.

Only use this configuration with trusted code. Shared Copilot sessions can
contain sensitive conversation content, and the GitHub configuration or
forwarded `GH_TOKEN` grants the container the token's GitHub permissions.

Both the container and VS Code processes run as `vscode`, with
`updateRemoteUserUID` enabled to match your host UID/GID on Linux. Normal tools
therefore create host-owned files, not root-owned files; avoid `sudo` for editing,
package installation into the virtual environment, or Copilot commands.
After changing this configuration, select **Dev Containers: Rebuild and Reopen
in Container**. Check `id` in the container terminal against `id` on the host.
Sharing the Copilot files does not transfer a running chat, provide Copilot
authentication, or guarantee that every VS Code chat provider can discover the
session. GitHub CLI authentication is shared separately as described above. Use
the provider's session-history/resume UI inside the container if supported, and
do not run the same resumed session simultaneously on the host and in a
container.

Start the full stack from the server repository as above. From this container,
the host gateway is `http://host.docker.internal:8080`; PostgreSQL published on
the host is reachable at `host.docker.internal:5432`. Integration tests still
require an explicitly configured **isolated test database**, not the development
or production database.

### Without a container

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
