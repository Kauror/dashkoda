"""The invariants the schema itself has to guarantee.

These are the rules that must hold whatever writes the row, so they are checked
against the model and the database rather than through a view.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.visibility.bootstrap import ensure_facebook_source, ensure_linkedin_source
from apps.visibility.models import (
    CollectionMethod,
    VisibilityEntryBatch,
    VisibilityMetric,
    VisibilityObservation,
    VisibilityRecordImmutable,
    WebsiteTrafficObservation,
)
from apps.visibility.registry import METRICS, spec_for

pytestmark = pytest.mark.django_db


# -- vocabulary ---------------------------------------------------------


def test_the_metric_vocabulary_is_explicit_and_closed():
    """Seven named metrics, and no way to invent an eighth by typing one."""
    assert set(VisibilityMetric.values) == {
        "newsletter_eteataja",
        "newsletter_enews",
        "newsletter_evestnik",
        "facebook_followers",
        "linkedin_followers",
        "instagram_followers",
        "youtube_subscribers",
    }


def test_the_registry_describes_exactly_the_stored_vocabulary():
    """`registry._check_registry` runs at import; this states the contract."""
    assert {spec.key for spec in METRICS} == set(VisibilityMetric.values)
    assert all(spec_for(value) is not None for value in VisibilityMetric.values)


def test_no_metric_is_stored_in_a_json_field():
    """The figures are typed columns, not keys in a blob.

    A JSON metric store would make "was this ever reported" unanswerable in SQL
    and would let a typo create a parallel series.
    """
    json_fields = [
        field.name
        for field in VisibilityObservation._meta.get_fields()
        if field.get_internal_type() == "JSONField"
    ]
    assert json_fields == []


def test_only_manual_collection_is_produced_by_this_release(submit):
    submit(facebook_followers=100)

    assert VisibilityObservation.objects.get().collection_method == CollectionMethod.MANUAL


# -- values -------------------------------------------------------------


def test_a_negative_value_is_refused_by_the_database(submit):
    batch = submit(facebook_followers=100)
    observation = VisibilityObservation.objects.get()

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            VisibilityObservation.objects.filter(pk=observation.pk).update(value=-1)
    assert batch.pk is not None


def test_zero_is_a_real_value_and_is_preserved(submit):
    submit(instagram_followers=0)

    stored = VisibilityObservation.objects.get()
    assert stored.value == 0
    assert stored.is_current_for_date is True


def test_a_metric_nobody_entered_has_no_row_at_all(submit):
    submit(facebook_followers=100)

    assert not VisibilityObservation.objects.filter(
        metric=VisibilityMetric.INSTAGRAM_FOLLOWERS
    ).exists()


# -- uniqueness ---------------------------------------------------------


def test_only_one_observation_is_current_per_metric_and_date(submit, today):
    submit(facebook_followers=100)
    submit(facebook_followers=200)

    current = VisibilityObservation.objects.filter(
        metric=VisibilityMetric.FACEBOOK_FOLLOWERS,
        observation_date=today,
        is_current_for_date=True,
    )
    assert current.count() == 1
    assert current.get().value == 200


def test_the_database_refuses_a_second_current_row_for_one_metric_and_date(submit, today):
    submit(facebook_followers=100)
    submit(facebook_followers=200)
    retired = VisibilityObservation.objects.filter(is_current_for_date=False).get()

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            VisibilityObservation.objects.filter(pk=retired.pk).update(is_current_for_date=True)


def test_one_batch_cannot_contain_the_same_metric_twice(submit):
    batch = submit(facebook_followers=100)
    existing = VisibilityObservation.objects.get()

    duplicate = VisibilityObservation(
        batch=batch,
        source=existing.source,
        artifact=existing.artifact,
        import_run=existing.import_run,
        metric=existing.metric,
        value=999,
        observation_date=existing.observation_date,
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            duplicate.save()


# -- metric and source agreement ----------------------------------------


def test_a_metric_must_carry_its_registered_source(submit):
    """LinkedIn figures may not be filed under the Facebook source.

    The mapping lives in the registry rather than in a table, so no database
    constraint can express it — `full_clean` is where it is enforced, and the
    publication service calls it for every row.
    """
    submit(facebook_followers=100)
    existing = VisibilityObservation.objects.get()
    wrong_source = ensure_linkedin_source()

    mismatched = VisibilityObservation(
        batch=existing.batch,
        source=wrong_source,
        artifact=existing.artifact,
        import_run=existing.import_run,
        metric=VisibilityMetric.FACEBOOK_FOLLOWERS,
        value=101,
        observation_date=existing.observation_date,
    )
    with pytest.raises(ValidationError) as error:
        mismatched.full_clean()
    assert "source" in error.value.message_dict


def test_the_registered_source_is_accepted(submit):
    submit(facebook_followers=100)

    assert VisibilityObservation.objects.get().source == ensure_facebook_source()


# -- immutability -------------------------------------------------------


def test_a_published_value_cannot_be_rewritten(submit):
    submit(facebook_followers=100)
    observation = VisibilityObservation.objects.get()
    observation.value = 1

    with pytest.raises(VisibilityRecordImmutable):
        observation.save()


def test_only_the_current_flag_may_change(submit):
    submit(facebook_followers=100)
    observation = VisibilityObservation.objects.get()

    observation.is_current_for_date = False
    observation.save(update_fields=["is_current_for_date"])

    assert VisibilityObservation.objects.get().is_current_for_date is False


def test_a_published_batch_cannot_be_changed(submit):
    batch = submit(facebook_followers=100)
    batch.note = "muudetud"

    with pytest.raises(VisibilityRecordImmutable):
        batch.save()


def test_deletion_is_refused_on_the_instance_and_on_the_queryset(submit):
    batch = submit(facebook_followers=100)
    observation = VisibilityObservation.objects.get()

    with pytest.raises(VisibilityRecordImmutable):
        observation.delete()
    with pytest.raises(VisibilityRecordImmutable):
        VisibilityObservation.objects.all().delete()
    with pytest.raises(VisibilityRecordImmutable):
        batch.delete()
    with pytest.raises(VisibilityRecordImmutable):
        VisibilityObservation.objects.bulk_update([observation], ["is_current_for_date"])

    assert VisibilityObservation.objects.count() == 1
    assert VisibilityEntryBatch.objects.count() == 1


# -- supersession -------------------------------------------------------


def test_a_same_date_correction_supersedes_the_previous_value(submit, today):
    submit(facebook_followers=100)
    submit(facebook_followers=140)

    rows = VisibilityObservation.objects.filter(
        metric=VisibilityMetric.FACEBOOK_FOLLOWERS, observation_date=today
    ).order_by("id")

    assert rows.count() == 2
    original, correction = rows
    # The original keeps every number it ever had.
    assert original.value == 100
    assert original.is_current_for_date is False
    assert correction.value == 140
    assert correction.is_current_for_date is True
    assert correction.supersedes_id == original.pk


def test_a_later_date_does_not_supersede_an_earlier_one(submit, today, days_ago):
    submit(observation_date=days_ago(30), facebook_followers=100)
    submit(observation_date=today, facebook_followers=140)

    rows = VisibilityObservation.objects.filter(
        metric=VisibilityMetric.FACEBOOK_FOLLOWERS
    ).order_by("observation_date")

    assert [row.value for row in rows] == [100, 140]
    # Both remain part of the history and both remain current for their own date.
    assert all(row.is_current_for_date for row in rows)
    assert all(row.supersedes_id is None for row in rows)


def test_an_observation_cannot_supersede_one_of_a_different_metric(submit):
    submit(facebook_followers=100, linkedin_followers=200)
    facebook = VisibilityObservation.objects.get(metric=VisibilityMetric.FACEBOOK_FOLLOWERS)
    linkedin = VisibilityObservation.objects.get(metric=VisibilityMetric.LINKEDIN_FOLLOWERS)

    facebook.supersedes = linkedin
    with pytest.raises(ValidationError) as error:
        facebook.full_clean()
    assert "supersedes" in error.value.message_dict


# -- website traffic ----------------------------------------------------


def test_nothing_publishes_website_traffic_yet():
    """Google Analytics is not connected, so the table is empty by design."""
    assert WebsiteTrafficObservation.objects.count() == 0


def _synthetic_traffic_provenance():
    """A source, artifact and import run for the traffic constraint tests.

    Built here rather than by any application code: nothing publishes website
    traffic yet, and this exists only so the constraints can be exercised
    against a row that is otherwise complete.
    """
    from apps.sources.services import build_import_run, register_external_reference
    from apps.visibility.bootstrap import ensure_ga4_source

    source = ensure_ga4_source()
    artifact = register_external_reference(
        source=source,
        external_reference="synthetic:ga4-constraint-test",
        original_name="synthetic.json",
        mime_type="application/json",
        sha256="a" * 64,
        size_bytes=10,
    )
    run = build_import_run(
        artifact=artifact,
        importer_name="synthetic_traffic_test",
        schema_version="1.0",
        dry_run=False,
    )
    return source, artifact, run


def test_a_traffic_period_may_not_end_before_it_starts(today):
    source, artifact, run = _synthetic_traffic_provenance()

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            WebsiteTrafficObservation.objects.create(
                source=source,
                artifact=artifact,
                import_run=run,
                observed_at=timezone.now(),
                period_start=today,
                period_end=today - timedelta(days=1),
            )


def test_a_coherent_traffic_period_is_accepted_and_then_immutable(today):
    source, artifact, run = _synthetic_traffic_provenance()

    observation = WebsiteTrafficObservation.objects.create(
        source=source,
        artifact=artifact,
        import_run=run,
        observed_at=timezone.now(),
        period_start=today - timedelta(days=7),
        period_end=today,
        sessions=1234,
        is_current=True,
    )

    observation.sessions = 1
    with pytest.raises(VisibilityRecordImmutable):
        observation.save()
    with pytest.raises(VisibilityRecordImmutable):
        observation.delete()


def test_only_one_traffic_observation_may_be_current_per_source(today):
    source, artifact, run = _synthetic_traffic_provenance()
    for index in range(2):
        try:
            with transaction.atomic():
                WebsiteTrafficObservation.objects.create(
                    source=source,
                    artifact=artifact,
                    import_run=run,
                    observed_at=timezone.now(),
                    period_start=today - timedelta(days=7 * (index + 1)),
                    period_end=today - timedelta(days=7 * index),
                    is_current=True,
                )
        except IntegrityError:
            assert index == 1, "the first current observation must be accepted"
            return
    raise AssertionError("a second current observation should have been refused")
