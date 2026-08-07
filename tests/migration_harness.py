"""Running a migration against a database that already holds rows.

Every other test in this suite runs against a freshly migrated database, and a
fresh database is the one case a backfill migration cannot fail in — there is
nothing to back-fill. That gap shipped a real failure. `legal_work` `0006` added
a unique `public_id` with a callable default, and production had thirty-four
catalogued documents:

    IntegrityError: could not create unique index
    DETAIL: Key (public_id)=(a477dbbd-…) is duplicated.

Django evaluates a callable default **once** when adding a column, so all
thirty-four rows received the same UUID, the backfill then found nothing null,
and the unique index refused. Nothing was harmed — the migration is atomic — but
1,892 passing tests had said nothing about it, because not one of them had a row
in that table before the migration ran.

**A fresh-database migration test and an upgrade-with-existing-data test are
different tests.** This module provides the second kind.

Use the `populated_migration` fixture from `tests/conftest.py`; see
`tests/legal_work/test_opinion_migrations.py` for a worked example and
`AGENTS.md` for when a migration is required to have one.

Two things are worth knowing about the mechanics:

- historical models are **not** the ones in `apps/`. `apps.get_model()` here
  returns the model as it stood at that migration, without custom managers,
  properties, `save()` overrides or validation. That is the point: the
  migration has to work against the table, not against today's Python;
- the test database is shared across the whole session, so a test that leaves
  an app at an older migration breaks every test after it. The fixture restores
  the leaf migration in teardown, including when the test fails.
"""

from __future__ import annotations

from collections.abc import Callable

from django.db import connection
from django.db.migrations.executor import MigrationExecutor

#: A migration, as Django's executor identifies one.
MigrationTarget = tuple[str, str]


def _executor() -> MigrationExecutor:
    """A fresh executor with a freshly built graph.

    Rebuilt every time rather than reused: applying a migration invalidates the
    loader's view of what is applied, and a stale graph silently migrates to the
    wrong place.
    """
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return executor


def migrate_to(target: MigrationTarget) -> None:
    """Apply or unapply migrations until `target` is the app's current state.

    Direction is inferred. Migrating backwards requires every operation in
    between to be reversible, which is worth knowing when a new migration's
    `RunPython` has no `reverse_code`: give it `migrations.RunPython.noop` when
    the forward step needs no undoing, and this harness can test it.
    """
    _executor().migrate([target])


def models_at(target: MigrationTarget):
    """The app registry exactly as it stood at `target`.

    Read models out of this with `.get_model(app_label, "ModelName")`, never
    from `apps.<app>.models` — importing today's model would test today's schema
    against yesterday's table.
    """
    return _executor().loader.project_state(target).apps


def leaf_migration(app_label: str) -> MigrationTarget:
    """The app's newest migration: where the test database must be left."""
    leaves = _executor().loader.graph.leaf_nodes(app_label)
    if len(leaves) != 1:
        raise RuntimeError(
            f"{app_label} has {len(leaves)} leaf migrations ({leaves}); "
            "the harness cannot tell which state to restore."
        )
    return leaves[0]


def run_populated_migration(
    app_label: str,
    *,
    before: str,
    after: str,
    seed: Callable[[object], None],
):
    """Migrate to `before`, insert rows, migrate to `after`.

    `seed` receives the historical app registry at `before` and is expected to
    create representative rows — the ones the migration will have to cope with.
    Returns the registry at `after`, so the test can read what the migration
    made of them.

    Prefer the `populated_migration` fixture, which additionally restores the
    database afterwards. This function is the mechanism; the fixture is the
    safe way to reach it.
    """
    migrate_to((app_label, before))
    seed(models_at((app_label, before)))
    migrate_to((app_label, after))
    return models_at((app_label, after))
