# DashKoda agent rules

These rules apply to the whole repository.

## Scope and delivery

- Read the current implementation brief and plan before changing the project.
- Implement only the named pull request; do not pull later milestones forward.
- Work on the branch named by the brief and use small, logical English commits.
- Open a draft pull request and do not merge it unless the user explicitly asks.
- Keep documentation truthful about what exists now and what is only planned.
- Report exact checks, deviations, blockers, commit hashes, and the next planned PR.

## Architecture boundaries

- Keep DashKoda a Django modular monolith with explicit module boundaries.
- Do not create placeholder Django apps for future modules.
- Keep the core liveness endpoint public, database-independent, and minimal.
- Do not use SQLite as a shortcut; PostgreSQL is the planned persistent database.
- Keep PostgreSQL on the private backend network and never publish its port to the host.
- Bind the local development web port to loopback unless a later brief explicitly changes it.
- Keep settings separated into base, local, test, and production modules.
- Production must never default to debug mode or a hard-coded secret.
- Use Estonian UI language and `Europe/Tallinn` application time.

## Data, security, and operations

- Never commit secrets, `.env` files, real member data, or production exports.
- Use only intentionally synthetic test data when later work requires fixtures.
- Protect application routes by default; keep public route exceptions exact and minimal.
- Keep `/admin/` behind viewer access as well as Django admin authentication.
- Store only a Django hash for the viewer PIN and use a version to invalidate sessions.
- Never persist or log raw client addresses for viewer rate limiting.
- Trust `CF-Connecting-IP` only when the explicit proxy-trust setting is enabled.
- Do not expose versions, dependencies, server details, or database state from health endpoints.
- Do not change Unraid, Docker runtime, Cloudflare, DNS, or production services unless
  the current brief explicitly authorizes it.
- Treat `dash.orgusaar.ee` as the planned production host, not as an existing deployment.

## Product and visual direction

- Keep future UI server-rendered and progressively enhanced as specified in the architecture.
- Follow the Chamber CVI when visual work is in scope; do not add logo assets before then.
- Use the lightweight system web-font stack documented in `docs/architecture.md`.

## Required checks

Before publishing a change, run the locked dependency install, Ruff format check,
Ruff lint, pytest, Django system check, and any brief-specific smoke tests.
