# Backend architecture

This guide consolidates API and design decisions from local backend and server
agent sessions, checked against the source on 2026-10-18. It describes the
implemented system, not every feature discussed in those sessions.
See [development.md](development.md) for environment setup and validation.

## Role in the system

The backend is a Python FastAPI REST service for onsite-print. It persists data
in PostgreSQL managed by the sibling database repository. nginx in the web-server
repository forwards `/api/` requests to `backend:8000`, preserving the API path.
The server repository orchestrates the full stack.

```text
Clients / frontend applications
              |
              v
     nginx web server
       /api/* proxy
              |
              v
     uvicorn / FastAPI
       /api/v2/*
              |
              v
    psycopg async pool
              |
              v
         PostgreSQL
```

Runtime dependencies are FastAPI, uvicorn, and psycopg with binary/pool support.
There is no ORM, separate repository layer, background worker, or PDF renderer
in the current backend. Routes use parameterized SQL directly.

## Source responsibilities

| Module | Responsibility |
|--------|----------------|
| [app/main.py](../app/main.py) | FastAPI application, lifespan, router registration, API prefix, health endpoint, JSON-safe validation errors |
| [app/config.py](../app/config.py) | Database connection settings and password-file loading |
| [app/db.py](../app/db.py) | Async pool, per-request connection dependency, schema upgrades, initial reference data |
| [app/events.py](../app/events.py) | Event request/response models and creation/list routes |
| [app/printers.py](../app/printers.py) | Printer models and creation/list/lookup routes |
| [app/nametag_types.py](../app/nametag_types.py) | Nametag types and supported-layout lookup |
| [app/layouts.py](../app/layouts.py) | Shared ordered layout-name catalogue |

Pydantic models live beside their routes. FastAPI dependency injection supplies
an async connection from `app.state.pool`. Route handlers execute SQL and validate
returned rows into response models. Connection contexts manage transactions:
normal exit commits and exceptional exit rolls back.

## Application lifecycle

1. FastAPI's lifespan creates the pool with automatic opening disabled.
2. The pool is opened and schema initialization runs before serving requests.
3. Schema initialization creates missing tables, applies explicit compatible
   upgrades, and seeds reference rows.
4. Requests borrow connections through `get_connection`.
5. Shutdown closes the pool. Schema initialization failures propagate and prevent
   successful startup rather than silently continuing.

Database credentials come from the settings documented in
[development.md](development.md#running-the-application-and-stack). A missing or
unreadable password file is not replaced with a silent default.

## API conventions

Keep the `/api/v2` prefix. Swagger UI is at `/api/v2/docs`, OpenAPI JSON at
`/api/v2/openapi.json`; ReDoc is disabled.

When implementing or changing endpoints, consult the previous version at
<https://api.printerboks.dk/api/v1/docs>. Reuse relevant conventions, field names,
request/response shapes, and behavior where sensible. Do not blindly copy legacy
behavior or switch back to `/api/v1`; explain meaningful deviations and clarify
incompatible requirements.

Current resource paths are singular. Collection routes use trailing slashes.
Creation uses `PUT` with status 201 by explicit project decision, not conventional
idempotent replacement semantics:

- Each event creation inserts a new row, including repeated identical requests.
- Duplicate printer or nametag-type codes return 409 without updating existing
  data.
- Missing keyed resources return 404.
- Invalid requests return 422. Resource input models trim surrounding whitespace
  and reject unknown fields.
- Empty collections return `[]`; ordering is explicit in SQL.

The validation-error handler renders nonfinite floats in error details as
strings, so rejecting `NaN`/infinity still produces valid JSON.

| Method | Path under `/api/v2` | Result |
|--------|----------------------|--------|
| GET | `/health` | Database version and `status: ok` after a successful query |
| PUT / GET | `/event/` | Create an event / list events by ID |
| PUT / GET | `/printer/` | Create a printer / list printers by code |
| GET | `/printer/{code}` | Look up one printer |
| PUT / GET | `/nametag_type/` | Create a type / list types by code |
| GET | `/nametag_type/{code}` | Look up one type |
| GET | `/nametag_type/{code}/layouts` | Supported layout names in catalogue order |
| GET | `/layout/` | All layout names in catalogue order |

Health checks query PostgreSQL; this is not a database-independent liveness
endpoint. There is no application-level authentication/authorization layer in
the currently registered routes.

## Data model

### Events

`event` has an identity-generated `BIGINT` primary key, required nonblank `name`,
and required `start_date`/`end_date` calendar dates. End date cannot precede start
date; a same-day event is valid.

Optional `street`, `zip`, `town`, and `country` are nullable text. Supplied values
must be nonblank; zip remains a string to preserve leading zeros and letters.
Responses include all address fields, using `null` when absent.

### Printers

`printer` is keyed by the full case-sensitive code. Codes consist of nonempty
alphanumeric parts separated by exactly one underscore. `label` is derived
from the suffix, including multi-digit labels, and is not supplied by clients.
Creation requires a nonblank description. Older custom rows may still return
`description: null`.

Startup seeds 13 printer codes from the legacy PDF project's
[printer_code.py](https://github.com/groskopf/printerbox_pdf/blob/3da0c8d91cb90f33ceae31471f431d94958080fb/printer_code.py).
The virtual printer is included. Codes found only in the legacy live API, rather
than that seed source, are not automatically added.

### Nametag types

`nametag_type` is keyed by a digit-only string code, preserving leading zeros.
It stores a required nonblank description, finite positive height/width in
millimetres, and a strict boolean `folded`.

Dimensions describe **one face**. Folding means repeating the face upside down,
not horizontal reflection; it does not double the stored height.

The two seeds preserve the legacy PDF template dimensions:

| Code | Height | Width | Folded |
|------|--------|-------|--------|
| `4760100` | 60 mm | 100 mm | false |
| `4786103` | 86 mm | 103 mm | true |

These are stored properties, not an implemented PDF-generation pipeline.

### Layouts and assignments

`layout` stores a unique name, nonnegative ordering position, and nullable
nonblank Danish description. `nametag_type_layout` links types and layouts
through foreign keys, with a composite primary key preventing duplicate links.

There are 35 seeded layouts, excluding the legacy `invalid` sentinel. Both
seeded types receive all 35 assignments. New types have no assignments by
default. Type-specific lookup distinguishes an unknown type (404) from a known
type with no assignments (`[]`). Layout order is `position`, then `name`.

GET responses expose layout names only, even though descriptions are stored.
The catalogue is shared rather than split into separate nametag/sheet tables.
There is no sheet-type table or sheet-layout mapping yet. These are reference
endpoints, not layout editing endpoints.

## Schema evolution and seed policy

The project currently uses explicit SQL upgrades in
[initialize_schema](../app/db.py), not a migration framework.
`CREATE TABLE IF NOT EXISTS` does not upgrade an existing table by itself.
Startup also:

- Adds nullable event address columns to existing tables.
- Adds printer/layout descriptions and fills missing descriptions for seeded
  rows without overwriting existing non-null descriptions.
- Renames `label_type` to `nametag_type`, preserving rows. If both tables exist,
  startup fails explicitly instead of choosing one.
- Renames the old `mirrored` column to `folded`, preserving its boolean values.
- Inserts missing nametag types, layouts, and seeded assignments without
  resetting custom rows or removing custom assignments.

Database constraints reinforce API validation: key formats, nonblank values,
date ordering, finite positive dimensions, derived printer labels, and foreign
keys. Normal upgrades must preserve existing PostgreSQL volumes; resetting data
is not the migration strategy.

The old `/api/v2/label_type/` routes are not aliases, and API payloads no longer
accept `mirrored`.

## Legacy design decisions and deferred work

Local sessions established these intentional choices:

- Use FastAPI behind the existing nginx proxy and retain `/api/v2`.
- Store printer/type/layout reference information in PostgreSQL instead of
  keeping it only in legacy Python enums/files.
- Name the resource `nametag_type`, not `label_type`, and use `folded`, not
  `mirrored`.
- Prefer the shared `/layout/` catalogue plus type-specific support lookup over
  the legacy separate `/layouts/name_tags` and `/layouts/sheets` responses.
  Printer and nametag-type CRUD are new surfaces where the legacy API does not
  provide equivalent standalone management routes.

Bookings were discussed, then deferred while preparing reference tables.
The discussed requirement was one printer per booking, with no overlapping date
intervals for the same printer. No booking table, endpoint, or overlap rule is
implemented yet; inclusive/exclusive interval boundaries still need agreement
before implementation. PDF generation, printing jobs, sheet-specific mappings,
and general update/delete operations are also not implemented here.

## Testing and deployment boundaries

[tests/](../tests) contains fake-connection API tests and optional real-PostgreSQL
tests for persistence, seed behavior, and schema upgrades. Integration tests
require an isolated test database; see [development.md](development.md#formatting-checking-and-tests).
Publishing-script tests use a mocked Docker CLI and are distinct from API tests.

The production image serves `app.main:app` with uvicorn on port 8000, running as
`nobody`. PostgreSQL data and reverse-proxy/frontend concerns belong to sibling
services. The development container deliberately layers tooling onto the same
runtime without adding those tools to the final production stage.

## Session sources

Architecture context came from local sessions in the server repository about
initial FastAPI/database wiring, `/api/v2`, events, addresses, printer seeds and
descriptions, nametag types, layout descriptions, and terminology changes.
Backend sessions supplied the runtime/development-image split and container
handoff findings. This is a curated export of durable decisions, not a copy of
private transcripts or an assertion that every historical request is complete.
