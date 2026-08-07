"""The populated-migration harness, tested on the migration that motivated it.

A harness that silently failed to move the database, or silently failed to put
it back, would be worse than none: the first makes every populated-migration
test vacuous, the second breaks whichever tests happen to run next. Both are
checked here.

`legal_work` `0005`→`0006` is used as the subject because it is the pair the
repository actually has — one that adds a field and back-fills it — so these
tests exercise a real backward migration rather than a contrived one.

The pair under test and the app's leaf are **not** the same thing. They happened
to coincide while `0006` was the newest migration, and naming the leaf as a
literal made every later migration break this file. The leaf is asked of the
migration graph instead.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder

from tests.migration_harness import leaf_migration, models_at

BEFORE = "0005_opinion_document_catalogue"
AFTER = "0006_opinion_matching_and_resources"


def leaf() -> tuple[str, str]:
    """Whatever `legal_work`'s newest migration is today."""
    return leaf_migration("legal_work")


def applied() -> set:
    return set(MigrationRecorder(connection).applied_migrations())


class TestItReachesTheHistoricalState:
    def test_the_model_before_the_migration_has_no_new_field(self, populated_migration):
        """Proof the seed really runs against the old table, not today's."""
        seen = {}

        def seed(apps):
            model = apps.get_model("legal_work", "OpinionDocumentBlob")
            seen["fields"] = {field.name for field in model._meta.get_fields()}

        populated_migration("legal_work", before=BEFORE, after=AFTER, seed=seed)

        assert "public_id" not in seen["fields"]

    def test_the_model_after_the_migration_has_it(self, populated_migration):
        apps = populated_migration("legal_work", before=BEFORE, after=AFTER, seed=lambda apps: None)
        model = apps.get_model("legal_work", "OpinionDocumentBlob")

        assert "public_id" in {field.name for field in model._meta.get_fields()}

    def test_the_migration_is_unapplied_while_the_seed_runs(self, populated_migration):
        recorded = {}

        def seed(apps):
            recorded["applied"] = applied()

        populated_migration("legal_work", before=BEFORE, after=AFTER, seed=seed)

        assert leaf() not in recorded["applied"], "the harness did not migrate backwards"
        assert ("legal_work", BEFORE) in recorded["applied"]

    def test_a_seed_that_raises_does_not_swallow_the_failure(self, populated_migration):
        """A broken seed must fail the test, not quietly produce an empty table."""

        def seed(apps):
            raise ValueError("synthetic seed failure")

        with pytest.raises(ValueError, match="synthetic seed failure"):
            populated_migration("legal_work", before=BEFORE, after=AFTER, seed=seed)


class TestItPutsTheDatabaseBack:
    """The test database is shared for the whole session.

    These two run in definition order, so the second observes the first one's
    teardown. That is the only way to see the restore from inside pytest, and it
    is the exact hazard: the previous version of the opinion migration test left
    `legal_work` at `0005` and was survivable only because its *other* test
    happened to migrate forward again.
    """

    def test_a_harness_test_runs_first(self, populated_migration):
        populated_migration("legal_work", before=BEFORE, after=AFTER, seed=lambda apps: None)

    def test_and_the_leaf_migration_is_applied_again_afterwards(self, db):
        assert leaf() in applied()

    def test_a_failing_harness_test_also_restores(self, populated_migration):
        """Teardown is a fixture's, so an exception in the body cannot skip it."""
        with pytest.raises(ValueError):
            populated_migration(
                "legal_work",
                before=BEFORE,
                after=AFTER,
                seed=lambda apps: (_ for _ in ()).throw(ValueError("synthetic")),
            )

    def test_and_the_leaf_is_still_applied_after_that_failure(self, db):
        assert leaf() in applied()


class TestTheHelpers:
    def test_the_leaf_migration_is_the_newest_one(self, db):
        """The highest-numbered migration on disk is the one the harness returns.

        Compared against the migration files rather than a literal, so adding a
        migration does not require editing this file — and stated as a `max` of
        a set that must be non-empty, so it cannot pass by comparing nothing.
        """
        import pathlib

        import apps.legal_work.migrations as package

        names = sorted(
            path.stem
            for path in pathlib.Path(package.__file__).parent.glob("0*.py")
            if path.stem != "__init__"
        )
        assert len(names) > 1, "this assertion is worthless with fewer than two migrations"
        assert leaf() == ("legal_work", names[-1])

    def test_an_unknown_app_has_no_leaf(self, db):
        with pytest.raises(Exception):
            leaf_migration("no_such_app")

    def test_models_at_returns_a_registry_not_todays_models(self, db):
        from apps.legal_work.opinion_models import OpinionDocumentBlob

        historical = models_at(leaf()).get_model("legal_work", "OpinionDocumentBlob")

        assert historical is not OpinionDocumentBlob
        assert historical._meta.db_table == OpinionDocumentBlob._meta.db_table
