# DashKoda

DashKoda is a planned internal management dashboard for the Estonian Chamber of
Commerce and Industry. It is intended for Chamber staff who need one consistent,
auditable view of operational and membership information.

The project is at the bootstrap stage. PR-01 establishes only the Django core,
split settings, developer tooling, tests, and a public liveness endpoint. It does
not implement the product dashboard or any business data.

## Local assumptions

- Python 3.14
- [`uv`](https://docs.astral.sh/uv/) for Python and dependency management
- no database or container runtime is required for PR-01
- all commands are run from the repository root

PR-01 deliberately uses Django's dummy database backend. It is nonpersistent
and exists only because Django expects a database setting. PostgreSQL replaces
it in PR-02; SQLite is not part of the planned architecture.

## Set up

Install the locked dependencies:

```powershell
uv sync --locked
```

## Quality checks

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pytest
uv run python manage.py check
```

To apply formatting locally:

```powershell
uv run ruff format .
```

## Run locally

```powershell
uv run python manage.py runserver
```

The public liveness endpoint is then available at:

```text
http://127.0.0.1:8000/health/live/
```

Its complete successful response is:

```json
{"status": "ok"}
```

## Planned later work

Later pull requests will add Docker and Unraid-compatible runtime packaging,
PostgreSQL, PIN-based access, server-rendered frontend foundations, membership
and dashboard modules, and the remaining agreed functionality. Production is
planned for `dash.orgusaar.ee`, but PR-01 makes no server, DNS, Cloudflare, or
deployment changes.

Do not commit secrets, `.env` files, production data, or real member data. The
repository must contain only synthetic fixtures when a later test explicitly
needs them.
