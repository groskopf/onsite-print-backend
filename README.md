# onsite-print-backend

The onsite-print REST API, built with FastAPI and served by uvicorn on port 8000.
It connects to the PostgreSQL database from `../onsite-print-database`.

## Documentation for developers and agents

Agents should read the [docs/](docs/) folder before working on this backend:

- [Development setup](docs/development.md): environment, tools, tests,
  troubleshooting, and known Copilot session-sharing limitations.
- [Architecture](docs/architecture.md): application structure, API conventions,
  data model, migrations, and deferred features.
- [Security plan](docs/security.md): proposed printer credentials, browser
  sessions, access levels, and backend authorization rules; not yet implemented.

These guides preserve project knowledge from local agent sessions so a new
session does not depend on access to previous chat history. Keep them updated
when changing the setup or architecture.

All routes live under `/api/v2`, so the same URLs work directly and through the
nginx web server, which forwards `/api/` to `backend:8000`:

- `GET /api/v2/health` – checks the database connection
- `POST /api/v2/events/` – creates an event and returns it with its generated ID and code (201)
- `GET /api/v2/events/` – returns all events, ordered by ID (an empty database returns `[]`)
- `GET /api/v2/events/{event_code}` – returns one event
- `PUT /api/v2/events/{event_code}` – replaces an event's fields and returns it
- `DELETE /api/v2/events/{event_code}` – deletes an event **and all its bookings and attendees**, returning the deleted event
- `/api/v2/docs` – interactive OpenAPI documentation
- `/api/v2/openapi.json` – OpenAPI schema

## Events

```sh
curl -X POST http://localhost:8080/api/v2/events/ \
  -H 'Content-Type: application/json' \
  -d '{"name":"Print conference","start_date":"2026-10-09","end_date":"2026-10-10"}'

curl http://localhost:8080/api/v2/events/
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

POST creates a new row on each call; repeating it creates another
event rather than updating an existing one.

Each event also gets a server-generated `code`: 16 uppercase base32 characters
(`A`–`Z`, `2`–`7`), the same format as booking codes. Clients cannot supply it.
Bookings reference their event through this code as `event_code`.

`GET`, `PUT`, and `DELETE` on `/api/v2/events/{event_code}` work with a single
event. PUT takes the same body as POST and replaces every field; omitted
address fields become `null`. The `id` and `code` never change. DELETE also
deletes every booking and attendee for the event. An unknown code returns 404; a malformed
code returns 422.

```sh
curl -X PUT http://localhost:8080/api/v2/events/ABCDEFGHIJKLMNOP \
  -H 'Content-Type: application/json' \
  -d '{"name":"Print conference","start_date":"2026-10-09","end_date":"2026-10-11"}'

curl -X DELETE http://localhost:8080/api/v2/events/ABCDEFGHIJKLMNOP
```

On startup the backend creates the initial `event` table if missing, including
database constraints for required fields, nonblank names, and date ordering.
This also works with an existing PostgreSQL volume; no data reset is needed.
Schema initialization errors fail application startup rather than being ignored.
Startup also adds the nullable address columns to existing tables using
`ADD COLUMN IF NOT EXISTS`. Existing events are preserved with `null` address
values; restarting is safe. Other future schema changes still need explicit
migrations: `CREATE TABLE IF NOT EXISTS` alone does not upgrade an existing schema.

## Attendees

Each event has its own attendee list:

- `POST /api/v2/attendees/{event_code}` creates an attendee (201). Add
  `?allow_duplicate=true` to create it even when a duplicate exists.
- `GET /api/v2/attendees/{event_code}` lists attendees by last name, first name,
  then code, or returns `[]` for an empty list.
- `GET /api/v2/attendees/{event_code}/{attendee_code}` returns one attendee.
- `PUT /api/v2/attendees/{event_code}/{attendee_code}` replaces the attendee's details.
- `DELETE /api/v2/attendees/{event_code}/{attendee_code}` returns the deleted attendee.

```json
{
  "first_name": "Ada",
  "last_name": "Lovelace",
  "address": "Main Street 12",
  "zip": "0123",
  "city": "Copenhagen",
  "country": "Denmark",
  "company": "Print company",
  "phone": "+45 01234567",
  "email": "ada@example.com"
}
```

First and last names are required. All other fields are optional, defaulting
to `null`. Strings are trimmed and cannot be blank; zip and phone remain strings.
Email is stored as a nonblank contact string, without email-format validation.
PUT uses the same body as POST; omitted optional fields become `null`.

POST rejects a duplicate with 409 unless `allow_duplicate=true`. An attendee is
a duplicate when the same event already has an attendee with the same first
name, last name, and email, compared case-insensitively after trimming. A
missing email only matches another missing email. The response identifies the
conflict with a machine-readable code and includes the existing attendee:

```json
{
  "detail": {
    "code": "attendee_exists",
    "message": "An attendee with the same name and email already exists",
    "attendee": {"code": "…", "event_code": "…", "first_name": "Ada", "last_name": "Lovelace", "…": "…"}
  }
}
```

Clients such as CSV import can ask the user, then retry with
`allow_duplicate=true`. PUT does not check for duplicates.

Responses include the details, `event_code`, and a server-generated `code`:
64 lowercase hexadecimal characters from 32 cryptographically random bytes
(256 bits). The code is unique across attendees, persists across restarts, and
cannot be supplied or changed by clients. It is reserved for future check-in;
no check-in status or actions are implemented yet.

An unknown event or an attendee outside that event returns 404. Malformed codes,
invalid fields, and unknown fields return 422. Startup creates the attendee table
and event index without resetting existing data. Deleting an event also deletes
its attendees through a database foreign-key cascade.

The legacy API has no attendee resource. These routes follow the current
event-scoped `/images/{event_code}` and `/sheets/{event_code}` conventions.
Attendees use the requested `address` and `city` fields, rather than the event
model's `street` and `town`.

## Printers

Printers are stored in a `printer` table keyed by their full code. When the
table is empty, startup seeds the 13 default codes from
[printer_code.py](https://github.com/groskopf/printerbox_pdf/blob/3da0c8d91cb90f33ceae31471f431d94958080fb/printer_code.py).
Once the table has any printer, startup never adds printers again, so deleted
default printers stay deleted. (Deleting every printer brings the defaults back
at the next restart.) The live legacy API also includes `Pey3NazUi3_B`, which is
absent from that source file and is not seeded here; it can be added through the API.

- `POST /api/v2/printers/` accepts
  `{"code":"NEWPRINTER_13","description":"New flight case"}` and returns
  `{"code":"NEWPRINTER_13","label":"13","description":"New flight case"}` with status 201.
- `GET /api/v2/printers/{code}` returns one printer, or 404 if it does not exist.
- `GET /api/v2/printers/` returns all printers sorted by code (or `[]`).
- `PUT /api/v2/printers/{code}` accepts `{"description":"Updated text"}` and returns
  the updated printer. Only the description can change: the code is the key that
  bookings refer to, and the label is derived from it. Sending `code` or `label`
  returns 422.
- `DELETE /api/v2/printers/{code}` deletes a printer and returns it. If any
  booking (past or future) uses the printer, it returns 409
  `"Printer has bookings"` and nothing is deleted.

Unknown printers return 404 for GET, PUT, and DELETE; malformed codes return 422.

The label is automatically derived from the suffix after `_`, including
multi-digit labels such as `"10"`. Codes are case-sensitive alphanumeric strings
with exactly one underscore separating a nonempty prefix and label. Creation
trims surrounding whitespace. Invalid codes and extra request fields return 422;
adding an existing code returns 409 without changing that record.
Labels are strings; no client-supplied label is needed.

New printers require a nonblank string `description`, trimmed on creation.
All printer responses include it. Startup adds the nullable description column
to older tables and fills missing descriptions for the default printer codes
that exist, without overwriting existing descriptions. Legacy custom printers without descriptions
remain readable with `description: null`. The seeded virtual printer is described
as "Virtual printer used to generate labels"; the others as
"1st generation black flight case".

The legacy API exposes codes as an enum used in booking routes but has no
standalone printer CRUD endpoints. These new routes follow the current API's
plural-resource, POST-creation, and PUT-update conventions instead.

## Nametag types

Nametag types replace the legacy
[NameTagType enum](https://github.com/groskopf/printerbox_pdf/blob/3da0c8d91cb90f33ceae31471f431d94958080fb/pdf/name_tag_type.py)
with a database-backed `nametag_type` table. The types are a **static list defined
in code** ([db.py](app/db.py)), because they will be bound to the nametag
generation code. The API is read-only: there is no POST, PUT, or DELETE. The two
types use the dimensions from the legacy PDF templates:

| Code | Description | Height (mm) | Width (mm) | Folded |
|------|-------------|-------------|------------|----------|
| `4760100` | Name tag 60x100 mm | 60 | 100 | false |
| `4786103` | Foldable name tag 86x103 mm | 86 | 103 | true |

Height and width describe **one face**, before padding. `folded` means an
upside-down duplicate for folding, not horizontally reflected text. Thus the
foldable type has a one-face height of 86 mm, not its unfolded height of 172 mm.
These are stored properties; PDF rendering is provided by the nametag endpoints.

- `GET /api/v2/nametag_types/{code}` returns one type, or 404 if absent.
- `GET /api/v2/nametag_types/` returns all types sorted by code (or `[]`).

Codes are digit-only strings (leading zeros are preserved). Every startup writes
the list from code into the table, overwriting any changed description,
dimensions, or `folded` value, so the code is the single source of truth. To add
or change a type, edit `INITIAL_NAMETAG_TYPES` and restart. Types created by the
removed `POST /api/v2/nametag_types/` endpoint are not deleted automatically,
because bookings may reference them.
Startup renames an existing `label_type` table to `nametag_type`, preserving its
records. If both tables exist, startup fails rather than choosing one silently.
The old `/api/v2/label_type/` endpoints are replaced, not retained as aliases.
Startup also renames the old `mirrored` column to `folded`, preserving its boolean
values. API requests and responses use only `folded`; `mirrored` is no longer
accepted.

The legacy API uses a fixed enum in bookings and layout routes and does not
expose nametag-type management endpoints; the read-only list matches that.

## Layouts

Layouts are shared between sheets and nametags, so there is one catalogue:

- `GET /api/v2/layouts/` returns layout names as a JSON array, for example
  `["layout_1", "layout_2", "layout_2PT", ...]`.
- `GET /api/v2/nametag_types/{code}/layouts` returns the supported layout names
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
custom assignments. A nametag type not in the static list has no assignments by default.

These are GET-only reference endpoints, not layout editing or PDF rendering.
Unlike the legacy API's separate `/layouts/name_tags` and `/layouts/sheets`
type-grouped lists, the catalogue is shared and type-specific support is exposed
under the nametag or sheet type. Sheet-specific assignments are stored in
`sheet_type_layout`; the implemented sheet type supports only `layout_1`.

## Nametags

Generated nametags are PDF files on disk, not database records. Each booking has
its own directory within its event, `$NAMETAGS_DIR/{event_code}/{booking_code}/`.
PDF filenames remain unique, so generating another nametag does not overwrite
previous ones.

- `POST /api/v2/nametags/{event_code}/{booking_code}` generates one nametag for the booking's
  nametag type and returns `201` with `{"filename": "<type>_<layout>_<uuid>.pdf"}`.
  The JSON body has `layout` and `line_1` (required) plus optional `line_2` to
  `line_5`, `image_name`, and `qr_code`; unknown fields return 422.
  Unknown bookings return 404. A booking that is not active today returns 400
  `Booking not valid today`. A layout that the nametag type does not support
  returns 400 `Name tag layout not supported`. An unsupported renderer type
  returns 404 `NameTagType not supported`.
- `GET /api/v2/nametags/{event_code}/{booking_code}` lists `[{"filename": ...}]`, oldest
  first. A booking without files returns `[]`.
- `DELETE /api/v2/nametags/{event_code}/{booking_code}` deletes all of the booking's files
  and returns the deleted list.
- `GET /api/v2/nametags/{event_code}/{booking_code}/{filename}` downloads one PDF
  (`application/pdf`). `DELETE` on the same path removes it and returns
  `{"filename": ...}`. Missing files return 404 `Nametag not found`.

Event codes, booking codes and filenames are validated (`<letters/digits/_/->.pdf`), so paths
cannot leave the booking directory; invalid values return 422.

Compared with the legacy `/name_tags` API: the path is `/nametags`, `layout`
moves from the query string into the body, and responses return the bare
filename rather than a relative path. Every operation checks that the booking
belongs to the URL's event; a missing booking/event or an event mismatch returns
404 `Booking not found`. Inactive bookings may still list/download/delete files;
only generation requires an active booking. Directory and file symlinks are
rejected. Printer-queue push is not implemented yet.

These event-scoped routes replace the former booking-only URLs. Existing PDFs
under `$NAMETAGS_DIR/{booking_code}/` are left untouched and are not listed or
served by the new routes. No automatic migration is performed. If a booking is
moved to another event or deleted, its old files remain on disk but are not
accessible through that booking's API.

The renderer in [app/nametag_pdf.py](app/nametag_pdf.py) ports all 35 legacy
layouts using ReportLab: text, pictures, QR codes, and picture/QR combinations.
Type `4760100` generates one 100 x 60 mm page. Type `4786103` generates one
103 x 172 mm page with two faces, the second rotated 180 degrees for folding.
Legacy text sizes, row proportions, padding, and shrink-to-fit behavior are
retained. The original `layout_2PTL` repetition of `line_3` is also retained.
Bottom QR codes now fit their own row instead of shrinking the entire row to
fit an oversized code. Text fields are rendered literally, not as ReportLab
markup; optional null fields render as empty text.

Pictures are read from `$IMAGES_DIR` (default `/var/lib/onsite-print/images`).
Upload pictures using the image API below. A supplied `image_name` is the
returned bare filename, resolved inside `$IMAGES_DIR/<event_code>/` using the
booking's event. Images from another event or the former global image directory
are not used. Missing files, traversal paths, and symlinks return 404
`Image not found`. Pictures retain their aspect ratio. Omitted pictures and
QR codes leave their layout cells empty.

```sh
curl -X POST http://localhost:8080/api/v2/nametags/QRSTUVWXYZ234567/ABCDEFGHIJKLMNOP \
  -H 'Content-Type: application/json' \
  -d '{"layout":"layout_3QT","line_1":"Ada Lovelace","line_2":"Engineer","line_3":"Analytical Engines","qr_code":"https://example.org/attendee/123"}'
```

## Images

Images are files associated with an existing event, stored under
`$IMAGES_DIR/<event_code>/`. No image records are stored in PostgreSQL.
All operations check that the event exists; unknown events return 404
`Event not found`, and malformed event codes return 422.

| Method | Path | Result |
|--------|------|--------|
| POST | `/api/v2/images/{event_code}` | Upload multipart field `image`; 201 with `{"filename": "<uuid>.<extension>"}` |
| GET | `/api/v2/images/{event_code}` | List filenames, oldest first; `[]` when empty |
| GET | `/api/v2/images/{event_code}/{filename}` | Download the image with its image media type |
| DELETE | `/api/v2/images/{event_code}/{filename}` | Delete one image; 200 with its filename, or 404 `Image not found` |

```sh
curl -X POST http://localhost:8080/api/v2/images/ABCDEFGHIJKLMNOP \
  -F 'image=@logo.png'
```

Uploads accept PNG, JPEG, GIF, WebP, BMP and TIFF up to **10 MiB
(10,485,760 bytes)** per image. The server verifies and decodes image contents;
the supplied filename and media type are not trusted. Files retain their
original bytes and get unique filenames with detected-format extensions:
`.png`, `.jpg`, `.gif`, `.webp`, `.bmp`, or `.tiff`. Uploading the same image
again creates a separate file rather than overwriting an existing one.
Empty/corrupt images and Pillow's excessive-dimension checks return 400;
recognized but unsupported formats return 415; oversized uploads return 413.
Temporary uploads are cleaned up on failure and only complete files are listed.
Animated or multipage images render their first frame/page in PDFs.

Pass the returned `filename` as `image_name` when generating a nametag or sheet
for the same event. Every supplied image name is checked before rendering,
even for layouts that do not display pictures. Omitting `image_name` remains
valid. Deleting an image does not alter already-generated PDFs.

Like generated nametags, image and sheet files are not automatically removed
when their event is deleted; the event-scoped endpoints then return 404.
Delete unwanted files before deleting the event. Authentication remains
deferred, matching the other `/api/v2` endpoints.

This retains the legacy image API's multipart `image` field and `filename`
response, but adds event scoping, validation and unique names, and returns 201
instead of 200 on upload. If using a reverse proxy, its request-body limit must
allow the 10 MiB image plus multipart overhead (for example, nginx
`client_max_body_size 11m`).

## Sheets

Sheets mirror the nametag file endpoints, but have **no booking or printer
association**. They belong to an event: generated PDFs live in
`$SHEETS_DIR/<event_code>/`, separate from nametags. All file operations check
that the event exists, returning 404 `Event not found` for unknown events.
The event code is supplied only in the URL, not repeated in the JSON body.

| Method | Path | Result |
|--------|------|--------|
| POST | `/api/v2/sheets/{event_code}` | Generate a sheet PDF; 201 with `{"filename": "<type>_<layout>_<uuid>.pdf"}` |
| GET | `/api/v2/sheets/{event_code}` | List the event's filenames, oldest first; `[]` when empty |
| DELETE | `/api/v2/sheets/{event_code}` | Delete all of the event's sheet PDFs and return the deleted filenames |
| GET | `/api/v2/sheets/{event_code}/{filename}` | Download a PDF (`application/pdf`) |
| DELETE | `/api/v2/sheets/{event_code}/{filename}` | Delete one PDF and return its filename |

POST accepts a JSON object containing `sheet_type`, `layout`, and a nonempty
`name_data_list`. Each entry requires `line_1` and accepts the same optional
`line_2` through `line_5`, `image_name`, and `qr_code` fields as a nametag.
Layout is supplied once for the sheet, not on individual entries. Unknown fields
(including `booking_code`) and invalid input return 422.

```sh
curl -X POST http://localhost:8080/api/v2/sheets/ABCDEFGHIJKLMNOP \
  -H 'Content-Type: application/json' \
  -d '{"sheet_type":"456090","layout":"layout_1","name_data_list":[{"line_1":"Ada Lovelace","line_2":"Engineer","line_3":"Analytical Engines"},{"line_1":"Grace Hopper","line_3":"US Navy"}]}'
```

The port of the legacy `sheet_456090` generator produces portrait A4 pages with
two columns of 90 x 60 mm labels, four rows (eight labels) per page. Entries
fill rows left to right; additional labels start new pages and an odd final
label leaves the right cell blank. Legacy `layout_1` uses the nametag
`layout_3PBL` composition: name/title/line 5, company, then picture on the left
with line 4 beside it. QR data is accepted but unused by this legacy layout.
Text styles, image validation, and shrink-to-fit behavior reuse the nametag
renderer. The port fixes legacy odd-list truncation and pagination.

Unknown sheet types return 404 `Name tag sheet type not supported`; layouts
other than `layout_1` return 400 `Name tag sheet layout not supported`. Invalid
or missing pictures return 404 `Image not found`; missing PDFs return 404
`Sheet not found`. Filenames use the same validation and symlink exclusion as
nametags. Writes are atomic and failed renders leave no partial files.
Images are looked up only in the selected event's image directory.
The former global sheet endpoints are replaced by these event-scoped routes;
existing global sheet PDFs are not migrated automatically.

### Sheet types

Sheet types are read-only database reference data, seeded from code on startup
like nametag types:

- `GET /api/v2/sheet_types/` lists types ordered by code.
- `GET /api/v2/sheet_types/{code}` returns one type.
- `GET /api/v2/sheet_types/{code}/layouts` lists supported layout names.

Unknown types return 404 `Sheet type not found`; malformed codes return 422.
Only `456090` is advertised because it is the only implemented legacy sheet
generator. Its description is `A4 sheet with 60x90 mm labels`, `height` is 60,
`width` is 90, and `folded` is false. Dimensions describe each label, not the
A4 page. Its supported-layout response is `["layout_1"]`.

Startup creates `sheet_type` and `sheet_type_layout` without changing existing
bookings, nametags, or layouts. Static type properties are refreshed on restart;
missing layout assignments are added without deleting existing assignments.
Both these reference endpoints and event checks for sheet files use PostgreSQL.

Compared with legacy sheets, type and layout move from query parameters into
the JSON body and responses contain bare filenames. Delete-all is added to
mirror nametags. Legacy enum types without generators are not advertised.
Authentication remains deferred, as for the current nametag endpoints.

## Configuration

| Variable           | Default                          |
|--------------------|----------------------------------|
| `DB_HOST`          | `postgres`                       |
| `DB_PORT`          | `5432`                           |
| `DB_NAME`          | `onsite_print`                   |
| `DB_USER`          | `onsite_print`                   |
| `DB_PASSWORD_FILE` | `/run/secrets/postgres_password` |
| `NAMETAGS_DIR`     | `/var/lib/onsite-print/nametags` |
| `IMAGES_DIR`       | `/var/lib/onsite-print/images` |
| `SHEETS_DIR`       | `/var/lib/onsite-print/sheets` |

In Compose the password is the `postgres_password` secret, read from
`../onsite-print-database/secrets/postgres_password`. Generated nametags are kept
in the `nametags` named volume mounted at `NAMETAGS_DIR`. Pictures are kept in
the separate `images` volume mounted at `IMAGES_DIR`. Sheet PDFs persist in
the `sheets` volume mounted at `SHEETS_DIR`.

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

Start PostgreSQL from the sibling database repository on the host before
creating or rebuilding the devcontainer:

```sh
cd ../onsite-print-database
docker compose up -d
```

This requires the database repository's configured image tag and password secret.
It also creates the `onsite-print-database_default` network that the devcontainer
joins to reach PostgreSQL directly.

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

To debug, select **Debug onsite-print backend** in the Run and Debug panel and
press **F5**. The configuration in [.vscode/launch.json](.vscode/launch.json)
runs `uvicorn` with the selected Python interpreter, connects to `postgres`
on the shared Docker network, and reads the password from the sibling database
repository's secret file. Set breakpoints, then make requests using
`http://localhost:8000/api/v2/docs`. Stop the debugger before restarting it;
reload is intentionally disabled for a single debugged process. Startup
initializes the schema and seeds reference data, so use a development database.

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
the host gateway is `http://host.docker.internal:8080`; PostgreSQL is reachable
at `postgres:5432` through the shared database network. Its host port is bound
to `127.0.0.1`, so `host.docker.internal:5432` is not reachable from the
devcontainer. Integration tests still
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
