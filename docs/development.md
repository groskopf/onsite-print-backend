# Development setup

This guide preserves development knowledge from local agent sessions, checked
against the repository on 2026-10-18. Read [architecture.md](architecture.md)
before changing application behavior. These documents are a portable handoff
for new sessions, including sessions started inside a devcontainer.

## Repository layout and prerequisites

The backend is a Python 3.12 FastAPI project. Related repositories are separate
sibling checkouts, not subdirectories to recreate inside this repository:

- `../onsite-print-server`: full-stack Compose orchestration.
- `../onsite-print-database`: PostgreSQL and its password secret.
- `../onsite-print-web-server`: nginx routing to the backend and frontend apps.
- Frontend repositories have their own Node.js development environments.

Keep these checkouts under a dedicated parent directory. The backend
devcontainer mounts that entire directory at its original absolute host path,
so sibling files and host-engine Compose bind mounts remain accessible.

## Preferred environment: VS Code devcontainer

Install Docker and the VS Code Dev Containers extension on the host. Open this
repository and run **Dev Containers: Reopen in Container**. After changing the
image or container configuration, use **Dev Containers: Rebuild and Reopen in
Container**.

Configuration lives in:

- [devcontainer.json](../.devcontainer/devcontainer.json): mounts, extensions,
  interpreter, save actions, forwarded port, and setup command.
- [Dockerfile](../Dockerfile): shared runtime and separate development/production
  stages.
- [pyproject.toml](../pyproject.toml): runtime and optional development packages.

The image stages intentionally separate responsibilities:

| Stage | Responsibility |
|-------|----------------|
| `runtime` | Python 3.12, `/opt/onsite-print-venv`, application and runtime dependencies |
| `development` | Runtime plus Git, GitHub CLI, curl, OpenSSH client, PostgreSQL client, jq, ripgrep, sudo, and the `vscode` user |
| `production` | Runtime only, runs uvicorn as `nobody` on port 8000 |

The devcontainer builds `development`, adds Docker CLI/Compose/Buildx through
the Docker-outside-of-Docker feature, and installs `.[dev]` in editable mode
after creation. It does not install Node.js or frontend packages.

Both container and VS Code processes run as `vscode`. Linux UID/GID matching is
enabled so files written through mounts remain host-owned. The virtual
environment is writable by that user; do not use `sudo` for project edits or
package installation.

Select `/opt/onsite-print-venv/bin/python` with **Python: Select Interpreter**
if VS Code retains a host interpreter. After changing dependencies:

```sh
python -m pip install -e '.[dev]'
```

Container setup does not start the application, start the stack, create secrets,
or reset database volumes. Port 8000 is forwarded when an application listens
there. Docker commands use the host engine, not a nested Docker daemon.

## Formatting, checking, and tests

The devcontainer installs Python/Pylance, Black, Flake8, and isort extensions.
On an explicit Python-file save, Black formats the file and isort organizes
imports. Black, isort, Flake8, and Ruff are configured for 120-character lines
in [pyproject.toml](../pyproject.toml) and [.flake8](../.flake8).

Installing a tool does not make every check run on save. In particular, mypy,
pytest, coverage, and pre-commit are not automatically run by the save actions.
No pre-commit hook is installed by the devcontainer configuration.

The [VS Code tasks](../.vscode/tasks.json) provide **Python: Run checks**, which
runs Flake8, mypy, and pytest using the selected interpreter. Invoke it through
**Tasks: Run Test Task**. Equivalent individual commands are:

```sh
python -m black app tests
python -m isort app tests
python -m flake8 app tests
python -m mypy app
python -m pytest
python -m pytest --cov=app
```

Ruff and pre-commit are available in the development dependency group, but are
not included in the aggregate task. Use the smallest relevant test selection
when validating a change, for example `python -m pytest tests/test_events.py`.

Unit tests use fake database connections. PostgreSQL integration tests are
skipped unless `TEST_DATABASE_CONNINFO` is set to psycopg connection parameters
for an **isolated test database**. Some tests create/drop temporary schemas;
the event persistence test intentionally leaves rows behind to verify that
data survives application restarts. Never point these tests at development or
production data.

## Running the application and stack

Normally start the full stack from `../onsite-print-server`. For full-stack
development, run there:

```sh
docker compose -f compose.yaml -f compose.dev.yaml up -d --build
```

That development setup mounts backend source and enables uvicorn reload,
alongside frontend hot reload. Rebuild images when dependencies change.
The backend repository's [compose.yaml](../compose.yaml) defines only the backend
and references a separately managed PostgreSQL service; it is not the complete
stack or a database provisioning command.

For manual execution from the devcontainer, first arrange database connectivity
and the password file, then run:

```sh
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Database environment variables and defaults are:

| Variable | Default |
|----------|---------|
| `DB_HOST` | `postgres` |
| `DB_PORT` | `5432` |
| `DB_NAME` | `onsite_print` |
| `DB_USER` | `onsite_print` |
| `DB_PASSWORD_FILE` | `/run/secrets/postgres_password` |

The password is read from a file, not a `DB_PASSWORD` environment variable.
Compose uses `../onsite-print-database/secrets/postgres_password` as its secret
source. A manually started process in the devcontainer does not automatically
receive that Compose secret: set `DB_PASSWORD_FILE` to the appropriate accessible
file and configure its database host explicitly.

From the devcontainer, the stack's host-published web endpoint is
`http://host.docker.internal:8080`; a host-published PostgreSQL port is reachable
at `host.docker.internal:5432`. These require the services and published ports
to be running. Once the backend is reachable, check `/api/v2/health` and browse
`/api/v2/docs`.

Without a container, use Python 3.12 or later:

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

## Copilot session sharing: what works and what does not

The current container configuration creates and bind-mounts these host
directories:

| Host path | Container path |
|-----------|----------------|
| `~/.copilot/session-state` | `/home/vscode/.copilot/session-state` |
| `~/.config/Code/agentSessionData` | `/home/vscode/.vscode-server/data/agentSessionData` |
| `~/.config/gh` | `/home/vscode/.config/gh` |

These mounts share files, not a running agent process, Copilot authentication,
or the complete VS Code session catalog. During the 2026-10-18 investigation,
the conversation was registered in the host's
`~/.config/Code/User/globalStorage/agent-host.db` but absent from the container's
`/home/vscode/.vscode-server/data/User/globalStorage/agent-host.db`.
Consequently, the conversation remained missing from the container's session
list even though its data files were mounted.

Rebuilding again does not fix that catalog mismatch. No supported migration
of this exact conversation was verified. Do not bind-mount the live host catalog
into another running Agent Host or resume the same session concurrently in both
environments. VS Code's Agents workspace picker has a **Use Dev Container**
option for creating a native container session; this is not a verified
existing-conversation transfer mechanism. Read these guides to hand off project
context to a new session without relying on local chat discovery.

GitHub CLI authentication is separate. Check it inside the container with
`gh auth status`. Host credentials stored in an OS keyring are not provided by
the `~/.config/gh` mount alone. The configuration forwards `GH_TOKEN` from the
environment that launches VS Code. Alternatively, GitHub CLI file-backed login
can be shared, but `gh auth login --insecure-storage` stores credentials in
plaintext and should only be used with that tradeoff understood.

All shared directories are writable. Sessions can contain sensitive content;
GitHub credentials grant their associated permissions. The parent-directory
mount and host Docker socket also expose host resources. Use trusted code only
and never put credentials or raw private transcripts into documentation.

## Troubleshooting learned from local sessions

- **VS Code server permission denied after container startup:** a nested bind
  mount can cause missing parent directories to be created as root. The
  development image now pre-creates `/home/vscode/.vscode-server/data`, owned by
  `vscode`, before Docker mounts the session data. Fix image ownership rather
  than running VS Code as root.
- **Image build fails in pip with `TOMLDecodeError`:** check
  [pyproject.toml](../pyproject.toml), not just the container configuration.
  A previous failure was caused by prose accidentally appended to the
  `[tool.setuptools]` header. Removing that text restored the development build.
- **Old conversations are absent:** distinguish shared data files from session
  registration as described above; this is separate from image build failures.

## Publishing

[docker-push.sh](../docker-push.sh) reads `IMAGE_TAG=major.minor.patch` from
`.env` (without leading zeros). Set `CR_PAT` in the environment, then run
`bash docker-push.sh`. It logs into GHCR, builds/pushes the backend, and increments
the patch in `.env` only after a successful push. Commit the incremented version;
deploy the tag printed as published, not the next version now in `.env`.
Do not publish concurrently or retry from stale version files.

`bash tests/docker-push-test.sh` tests the publishing script with a mocked Docker
CLI. The repository's GitHub Actions workflow runs these script tests; it is not
a full Python test/build pipeline and does not upload images.

## Session sources

This guide consolidates the local backend sessions about Python tooling,
format-on-save, line length, GitHub authentication, Copilot sharing, and failed
container rebuilds from 2026-10-17/18. Related local sessions in the server
repository supplied the multi-repository Compose context. Historical claims
were checked against current configuration; unresolved chat migration is
explicitly retained as a limitation rather than recorded as a successful fix.
