"""Migrations applied to a database that already holds a registered source.

`0006` renames the newsletter `DataSource` from `manual-smaily-audience` to
`smaily-newsletter-audience` and retypes it, because the figures stopped being
typed and started being collected. A fresh database never exercises that: it has
no source row to rename, so the migration is a no-op and says nothing about the
one deployment that matters — the one that has been running the manual form.

These tests give it that row. What they pin down is the pair of failures the
rename could otherwise cause silently:

- an observation losing its source, which would orphan every newsletter figure
  ever published;
- a rename onto a slug that already exists, which the unique index would refuse
  and which would take the whole upgrade down.
"""

from __future__ import annotations

BEFORE = "0005_smaily_newsletter_audience"
AFTER = "0006_rename_smaily_source"

OLD_SLUG = "manual-smaily-audience"
NEW_SLUG = "smaily-newsletter-audience"


def seed_manual_source(*, also_new: bool = False):
    """Register the source the way the manual-entry era left it."""

    def seed(apps):
        DataSource = apps.get_model("sources", "DataSource")
        DataSource.objects.create(
            slug=OLD_SLUG,
            name="Smaily uudiskirjade auditoorium",
            source_type="manual",
            expected_update_frequency="irregular",
            description="Käsitsi sisestatud.",
        )
        if also_new:
            DataSource.objects.create(
                slug=NEW_SLUG,
                name="Smaily uudiskirjade auditoorium",
                source_type="other",
                expected_update_frequency="irregular",
                description="Juba ümber nimetatud.",
            )

    return seed


def sources(apps):
    return apps.get_model("sources", "DataSource").objects


def test_the_manual_source_is_renamed_and_retyped(populated_migration):
    apps = populated_migration("visibility", before=BEFORE, after=AFTER, seed=seed_manual_source())

    assert not sources(apps).filter(slug=OLD_SLUG).exists()
    renamed = sources(apps).get(slug=NEW_SLUG)
    assert renamed.source_type == "other"
    assert "sync_smaily" in renamed.description


def test_the_rename_keeps_the_same_row_so_nothing_is_orphaned(populated_migration):
    """The source is renamed, not replaced.

    A migration that deleted the old row and created a new one would leave every
    published newsletter observation pointing at a source that no longer exists
    — or, with `PROTECT`, would fail the upgrade outright.
    """
    seen = {}

    def seed(apps):
        seed_manual_source()(apps)
        seen["pk"] = sources(apps).get(slug=OLD_SLUG).pk

    apps = populated_migration("visibility", before=BEFORE, after=AFTER, seed=seed)

    assert sources(apps).get(slug=NEW_SLUG).pk == seen["pk"]


def test_a_deployment_that_already_has_both_slugs_is_left_alone(populated_migration):
    """Renaming onto an existing slug would violate the unique index.

    This is reachable: a deployment that ran the new bootstrap before the
    migration has both rows.
    """
    apps = populated_migration(
        "visibility", before=BEFORE, after=AFTER, seed=seed_manual_source(also_new=True)
    )

    assert sources(apps).filter(slug=NEW_SLUG).count() == 1
    # The old row is left in place rather than merged: choosing which of two
    # populated sources wins is not a decision a migration may make silently.
    assert sources(apps).filter(slug=OLD_SLUG).count() == 1


def test_a_database_with_no_smaily_source_migrates_cleanly(populated_migration):
    apps = populated_migration("visibility", before=BEFORE, after=AFTER, seed=lambda apps: None)
    assert not sources(apps).filter(slug__in=[OLD_SLUG, NEW_SLUG]).exists()
