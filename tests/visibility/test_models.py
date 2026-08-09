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
    Ga4DailySnapshot,
    Ga4PageDaily,
    VisibilityEntryBatch,
    VisibilityMetric,
    VisibilityObservation,
    VisibilityRecordImmutable,
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


# -- Google Analytics reporting days -------------------------------------


def test_the_ga4_history_starts_empty():
    """Nothing but the `sync_ga4` command ever writes these tables."""
    assert Ga4DailySnapshot.objects.count() == 0
    assert Ga4PageDaily.objects.count() == 0


def _synthetic_ga4_provenance():
    """A source, artifact and import run for the constraint tests.

    Built here rather than by application code: these tests exercise what the
    database refuses, and going through the publication service would test the
    service instead.
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
        schema_version="2.0",
        dry_run=False,
    )
    return source, artifact, run


def _day(source, artifact, run, *, report_date, current=False, revision=1, **figures):
    return Ga4DailySnapshot.objects.create(
        source=source,
        artifact=artifact,
        import_run=run,
        report_date=report_date,
        observed_at=timezone.now(),
        checksum="b" * 64,
        revision=revision,
        is_current_for_date=current,
        **figures,
    )


def test_a_published_day_is_immutable_apart_from_which_revision_is_current(today):
    source, artifact, run = _synthetic_ga4_provenance()

    day = _day(source, artifact, run, report_date=today, current=True, sessions=1234)

    day.sessions = 1
    with pytest.raises(VisibilityRecordImmutable):
        day.save()
    with pytest.raises(VisibilityRecordImmutable):
        day.delete()

    # The one field that may move, and the reason it may: a revision retires.
    day.refresh_from_db()
    day.is_current_for_date = False
    day.save(update_fields=["is_current_for_date"])


def test_two_revisions_of_one_day_may_not_both_be_current(today):
    """The invariant the whole history rests on. Two current revisions would be
    a day with two truths, and every chart crossing it would count it twice."""
    source, artifact, run = _synthetic_ga4_provenance()
    _day(source, artifact, run, report_date=today, current=True)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _day(source, artifact, run, report_date=today, current=True, revision=2)


def test_two_days_may_each_have_their_own_current_revision(today):
    """What the retired model could not do: `is_current` was unique per source,
    so publishing yesterday retired the day before it."""
    source, artifact, run = _synthetic_ga4_provenance()

    _day(source, artifact, run, report_date=today, current=True)
    _day(source, artifact, run, report_date=today - timedelta(days=1), current=True)

    assert Ga4DailySnapshot.objects.filter(is_current_for_date=True).count() == 2


def test_a_superseded_revision_stays_readable_beside_the_current_one(today):
    source, artifact, run = _synthetic_ga4_provenance()

    first = _day(source, artifact, run, report_date=today, sessions=100)
    second = _day(source, artifact, run, report_date=today, current=True, revision=2, sessions=140)
    second.supersedes = second.supersedes  # no-op; the link is set at publication

    assert Ga4DailySnapshot.objects.filter(report_date=today).count() == 2
    first.refresh_from_db()
    assert first.sessions == 100, "the earlier reading keeps its figures"


def test_a_page_row_cannot_be_rewritten(today):
    source, artifact, run = _synthetic_ga4_provenance()
    day = _day(source, artifact, run, report_date=today, current=True)

    row = Ga4PageDaily.objects.create(
        snapshot=day, report_date=today, path="/et/uudised/a", page_views=10
    )

    row.page_views = 11
    with pytest.raises(VisibilityRecordImmutable):
        row.save()


def test_a_path_appears_once_per_day(today):
    """Canonicalisation folds `/x`, `/x/` and `/x?utm=…` into one row before
    they reach here; this is the database refusing what would slip through."""
    source, artifact, run = _synthetic_ga4_provenance()
    day = _day(source, artifact, run, report_date=today, current=True)
    Ga4PageDaily.objects.create(
        snapshot=day, report_date=today, path="/et/uudised/a", page_views=10
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Ga4PageDaily.objects.create(
                snapshot=day, report_date=today, path="/et/uudised/a", page_views=5
            )


def test_the_engagement_rate_is_derived_and_absent_when_there_is_nothing_to_divide(today):
    """Not a column: a stored quotient is a second answer to a question the two
    counts already answer, and the two drift."""
    source, artifact, run = _synthetic_ga4_provenance()

    engaged = _day(source, artifact, run, report_date=today, sessions=200, engaged_sessions=120)
    assert engaged.engagement_rate == pytest.approx(0.6)

    quiet = _day(source, artifact, run, report_date=today - timedelta(days=1), sessions=0)
    assert quiet.engagement_rate is None, "no sessions is not an engagement rate of zero"
