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
There is no ORM, separate repository layer, or background worker, and the PDF
renderer is still a stub. Routes use parameterized SQL directly.

## Source responsibilities

| Module | Responsibility |
|--------|----------------|
| [app/main.py](../app/main.py) | FastAPI application, lifespan, router registration, API prefix, health endpoint, JSON-safe validation errors |
| [app/config.py](../app/config.py) | Database connection settings, password-file loading, nametag directory |
| [app/db.py](../app/db.py) | Async pool, per-request connection dependency, schema upgrades, initial reference data |
| [app/events.py](../app/events.py) | Event request/response models and create/list/lookup/update/delete routes |
| [app/printers.py](../app/printers.py) | Printer models and create/list/lookup/update/delete routes |
| [app/nametag_types.py](../app/nametag_types.py) | Read-only nametag types and supported-layout lookup |
| [app/layouts.py](../app/layouts.py) | Shared ordered layout-name catalogue |
| [app/bookings.py](../app/bookings.py) | Printer booking models and create/list/lookup/update/delete routes |
| [app/nametags.py](../app/nametags.py) | Generated nametag PDF files per booking: create/list/download/delete |
| [app/nametag_pdf.py](../app/nametag_pdf.py) | `NameData` request model and the `render_nametag` PDF renderer (not implemented yet) |
| [app/codes.py](../app/codes.py) | Generated 16-character base32 codes for events and bookings |

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

Resource paths use plural collection names (`/events/`, `/printers/`,
`/nametag_types/`, `/layouts/`, `/bookings/`), and element paths nest under them
(for example `/printers/{code}`). The earlier singular paths (`/event/`,
`/printer/`, `/nametag_type/`, `/layout/`) are not kept as aliases.
Collection routes use trailing slashes. HTTP methods follow these rules:

- `POST` on a collection creates/adds a new element and returns 201 with it.
- `PUT` on an element path (for example `/bookings/{booking_code}`) updates an
  existing element; it must not create new elements. A missing element returns
  404. Events, printers, and bookings have update routes. Nametag types and
  layouts are read-only.
- `DELETE` on an element path removes it and returns 200 with the deleted
  element; a missing element returns 404.
- `GET` reads a collection or an element.

Request payloads are JSON bodies, including bookings, rather than the legacy
API's query parameters. Further conventions:

- Each event creation inserts a new row, including repeated identical requests.
- Duplicate printer codes return 409 without updating existing data.
- Deleting a printer that any booking uses returns 409.
- Missing keyed resources return 404.
- Generating a nametag for a booking not active today, or with a layout the
  booking's nametag type does not support, returns 400 (as in the legacy API).
- A booking that overlaps another booking for the same printer returns 409.
- Invalid requests return 422. Resource input models trim surrounding whitespace
  and reject unknown fields.
- Empty collections return `[]`; ordering is explicit in SQL.

The validation-error handler renders nonfinite floats in error details as
strings, so rejecting `NaN`/infinity still produces valid JSON.

| Method | Path under `/api/v2` | Result |
|--------|----------------------|--------|
| GET | `/health` | Database version and `status: ok` after a successful query |
| POST / GET | `/events/` | Create an event / list events by ID |
| GET / PUT / DELETE | `/events/{event_code}` | Look up / replace / delete one event (delete also removes its bookings) |
| POST / GET | `/printers/` | Create a printer / list printers by code |
| GET / PUT / DELETE | `/printers/{code}` | Look up / update description / delete one printer (409 if it has bookings) |
| GET | `/nametag_types/` | List the static types by code |
| GET | `/nametag_types/{code}` | Look up one type |
| GET | `/nametag_types/{code}/layouts` | Supported layout names in catalogue order |
| GET | `/layouts/` | All layout names in catalogue order |
| POST / GET | `/bookings/` | Create a printer booking / list bookings by start date, printer code |
| GET / PUT / DELETE | `/bookings/{booking_code}` | Look up / replace / delete one booking |
| POST / GET / DELETE | `/nametags/{booking_code}` | Generate a nametag PDF / list / delete all of the booking's files |
| GET / DELETE | `/nametags/{booking_code}/{filename}` | Download / delete one nametag PDF |

Health checks query PostgreSQL; this is not a database-independent liveness
endpoint. There is no application-level authentication/authorization layer in
the currently registered routes.

## Data model

### Events

`event` has an identity-generated `BIGINT` primary key, a unique server-generated
`code` (16 uppercase base32 characters, the same format as booking codes),
required nonblank `name`, and required `start_date`/`end_date` calendar dates.
End date cannot precede start date; a same-day event is valid. Bookings reference
events by `code`, not by the numeric ID.

`PUT /events/{event_code}` replaces all client fields; `id` and `code` are kept.
`DELETE /events/{event_code}` removes the event and all of its bookings in one
transaction and returns the deleted event. The cascade is done explicitly in the
route, not with `ON DELETE CASCADE`, so the schema needs no migration. The route
locks the event row (`FOR UPDATE`) first, and booking create/update takes
`FOR KEY SHARE` on the event it references. A booking write that races a delete
therefore either commits first and is deleted with the event, or waits and then
gets 404 "Event not found".

Optional `street`, `zip`, `town`, and `country` are nullable text. Supplied values
must be nonblank; zip remains a string to preserve leading zeros and letters.
Responses include all address fields, using `null` when absent.

### Printers

`printer` is keyed by the full case-sensitive code. Codes consist of nonempty
alphanumeric parts separated by exactly one underscore. `label` is derived
from the suffix, including multi-digit labels, and is not supplied by clients.
Creation requires a nonblank description. Older custom rows may still return
`description: null`.

`PUT /printers/{code}` changes only `description`; the code is the key bookings
reference and the label is derived from it. `DELETE /printers/{code}` returns 409
"Printer has bookings" if any booking uses the printer. It locks the printer row
first; booking create/update lock the same row, so a booking cannot be added
between the check and the delete.

When the `printer` table is empty, startup seeds 13 default printer codes from the
legacy PDF project's
[printer_code.py](https://github.com/groskopf/printerbox_pdf/blob/3da0c8d91cb90f33ceae31471f431d94958080fb/printer_code.py).
If the table has any rows, no printers are added, so deleted defaults stay
deleted. The check runs under a table lock so concurrent startups cannot both
seed. The virtual printer is included. Codes found only in the legacy live API,
rather than that seed source, are not automatically added.

### Nametag types

`nametag_type` is keyed by a digit-only string code, preserving leading zeros.
It stores a required nonblank description, finite positive height/width in
millimetres, and a strict boolean `folded`.

Dimensions describe **one face**. Folding means repeating the face upside down,
not horizontal reflection; it does not double the stored height.

Nametag types are a static list in code (`INITIAL_NAMETAG_TYPES` in
[app/db.py](../app/db.py)), because they will be bound to the nametag generation
code. The API only reads them. Every startup upserts the list, overwriting changed
values, so the code is the source of truth. Rows not in the list (for example,
created through the removed POST endpoint) are left in place because bookings may
reference them. The two types preserve the legacy PDF template dimensions:

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
static types receive all 35 assignments. Types outside the static list have no
assignments by default. Type-specific lookup distinguishes an unknown type (404) from a known
type with no assignments (`[]`). Layout order is `position`, then `name`.

GET responses expose layout names only, even though descriptions are stored.
The catalogue is shared rather than split into separate nametag/sheet tables.
There is no sheet-type table or sheet-layout mapping yet. These are reference
endpoints, not layout editing endpoints.

### Bookings

`booking` reserves a printer for an inclusive date range. It is keyed by a
server-generated `booking_code`: 16 uppercase base32 characters derived from a
random UUID, like the legacy format but one character longer (legacy codes
had 15). Shared code generation lives in
[app/codes.py](../app/codes.py). The required `event_code` references
`event(code)`; `printer_code` and `nametag_type` reference `printer` and
`nametag_type`. The legacy field was `name_tag_type`, and legacy bookings had no
event. Clients supply `event_code`, `printer_code`, `nametag_type`,
`start_date`, and `end_date`.

End date cannot precede start date; one-day bookings are valid. Booking dates are
not required to fall within the event's dates. Bookings for the
same printer may not overlap, including sharing a boundary day. Creation and
update lock the target printer row before checking overlaps, so concurrent
requests for one printer cannot both succeed. Unknown events, printers, or
nametag types return 404.

`PUT /bookings/{booking_code}` replaces all five client fields and keeps the
booking code. The overlap check ignores the booking being
updated, so a booking can be shortened, extended, or moved to another printer.
`DELETE` returns the deleted booking. Booking codes in paths must match the
generated format; malformed codes return 422, unknown ones 404.

The legacy current-printer-booking route (`/bookings/printer/{printer_code}`) and
API-key authentication are not implemented yet.

### Generated nametags

Nametags are files, not rows. `POST /nametags/{booking_code}` reads the booking
and the `nametag_type_layout` assignment, then renders to
`$NAMETAGS_DIR/{booking_code}/{nametag_type}_{layout}_{uuid}.pdf` (the legacy
naming). Rendering runs in a worker thread and writes a hidden temporary file
that is atomically renamed, so a failed render leaves no partial PDF. List,
download, and delete use only the filesystem: they accept regular `*.pdf` files
whose names match the filename pattern, ignore symlinks and other entries, and
list oldest first. Deleting all files also removes the empty booking directory.

In containers `NAMETAGS_DIR` is `/var/lib/onsite-print/nametags`, owned by the
runtime user and mounted as the `nametags` named volume in Compose.

## Schema evolution and seed policy

The project currently uses explicit SQL upgrades in
[initialize_schema](../app/db.py), not a migration framework.
`CREATE TABLE IF NOT EXISTS` does not upgrade an existing table by itself.
Startup also:

- Adds nullable event address columns to existing tables.
- Adds the event `code` column, generates codes for existing events, then makes
  it required.
- Adds `event_code` to an existing `booking` table and makes it required. If
  bookings without an event already exist, startup fails explicitly rather than
  guessing an event.
- Adds printer/layout descriptions and fills missing descriptions for default
  rows that exist, without overwriting existing non-null descriptions.
- Seeds default printers only into an empty `printer` table.
- Renames `label_type` to `nametag_type`, preserving rows. If both tables exist,
  startup fails explicitly instead of choosing one.
- Renames the old `mirrored` column to `folded`, preserving its boolean values.
- Upserts the static nametag types, overwriting changed values.
- Inserts missing layouts and seeded assignments without resetting custom rows
  or removing custom assignments.

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
- Prefer the shared `/layouts/` catalogue plus type-specific support lookup over
  the legacy separate `/layouts/name_tags` and `/layouts/sheets` responses.
  Printer CRUD is a new surface where the legacy API does not provide equivalent
  standalone management routes. Nametag types stay a fixed list, like the legacy
  enum, and are read-only in the API.

Bookings were initially deferred while preparing reference tables; they are now
implemented with one printer per booking and inclusive, non-overlapping date
intervals per printer (see [Bookings](#bookings)). Nametag file endpoints exist
(see [Generated nametags](#generated-nametags)), but these are deferred:

- The PDF renderer, `render_nametag` in [app/nametag_pdf.py](../app/nametag_pdf.py);
  until it is ported from the legacy `pdf/name_tag_*.py`, generation returns 501.
- The legacy image-existence check for `image_name` (no images endpoint yet).
- Pushing new nametags to a printer queue (legacy websocket).
- Removing a booking's nametag directory when the booking or its event is deleted.
- Printing jobs, sheet-specific mappings, and editing nametag types or layouts.

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
