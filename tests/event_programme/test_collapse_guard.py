"""The event programme refuses a workbook that collapses against what is published.

The failure this guards against is not a crash: a generator that stops matching
most of its source rows still produces a valid workbook, just a much emptier
one. Every assertion here is about the published snapshot surviving that.
"""

import pytest

from apps.event_programme.models import EventProgrammeItem, EventProgrammeSnapshot

from .conftest import FakeDownloader, synthetic_programme

pytestmark = pytest.mark.django_db


def _current():
    return EventProgrammeSnapshot.objects.filter(is_current=True).first()


def test_a_collapsed_workbook_is_refused_and_the_published_snapshot_survives(
    publish_programme, make_workbook
):
    from apps.event_programme.sync import synchronize_public_workbook

    publish_programme(rows=synthetic_programme())
    before = _current()
    assert before.canonical_event_count == 9

    # One row where nine were published: the shape of a generator that has
    # silently stopped producing records.
    collapsed = make_workbook(rows=synthetic_programme()[:1])
    outcome = synchronize_public_workbook(downloader=FakeDownloader(collapsed))

    assert outcome.result != "imported"

    after = _current()
    assert after.pk == before.pk
    assert after.canonical_event_count == 9
    assert EventProgrammeItem.objects.filter(snapshot=after).count() == 9
    assert EventProgrammeSnapshot.objects.filter(is_current=True).count() == 1


def test_the_refusal_names_both_counts_and_the_way_out(publish_programme, make_workbook):
    from apps.event_programme.importer import EventProgrammeImportError, import_artifact
    from apps.sources.services import register_external_reference

    publish_programme(rows=synthetic_programme())

    collapsed = make_workbook(rows=synthetic_programme()[:1])
    artifact = register_external_reference(
        source=_current().source,
        external_reference="synthetic:collapse",
        original_name="dashkoda_events.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        sha256="0" * 64,
        size_bytes=collapsed.stat().st_size,
    )

    with pytest.raises(EventProgrammeImportError) as raised:
        import_artifact(artifact, workbook_path=collapsed, dry_run=False)

    message = str(raised.value)
    assert "1" in message and "9" in message
    assert "--allow-collapse" in message


def test_allow_collapse_publishes_the_smaller_programme(publish_programme, make_workbook):
    from apps.event_programme.sync import synchronize_public_workbook

    publish_programme(rows=synthetic_programme())

    collapsed = make_workbook(rows=synthetic_programme()[:1])
    outcome = synchronize_public_workbook(downloader=FakeDownloader(collapsed), allow_collapse=True)

    assert outcome.result == "imported"
    assert _current().canonical_event_count == 1


def test_growth_is_never_refused(publish_programme, make_workbook):
    from apps.event_programme.sync import synchronize_public_workbook

    publish_programme(rows=synthetic_programme()[:2])
    assert _current().canonical_event_count == 2

    grown = make_workbook(rows=synthetic_programme())
    outcome = synchronize_public_workbook(downloader=FakeDownloader(grown))

    assert outcome.result == "imported"
    assert _current().canonical_event_count == 9


def test_a_first_import_has_nothing_to_collapse_against(publish_programme):
    outcome = publish_programme(rows=synthetic_programme()[:1])

    assert outcome.result == "imported"
    assert _current().canonical_event_count == 1


def test_a_shrink_within_the_allowed_ratio_still_publishes(publish_programme, make_workbook):
    from apps.event_programme.sync import synchronize_public_workbook

    publish_programme(rows=synthetic_programme())

    # Five of nine is above the half floor, so this is ordinary variation.
    smaller = make_workbook(rows=synthetic_programme()[:5])
    outcome = synchronize_public_workbook(downloader=FakeDownloader(smaller))

    assert outcome.result == "imported"
    assert _current().canonical_event_count == 5


def test_a_dry_run_reports_the_refusal_rather_than_passing_quietly(
    publish_programme, make_workbook
):
    from apps.event_programme.sync import synchronize_public_workbook

    publish_programme(rows=synthetic_programme())

    collapsed = make_workbook(rows=synthetic_programme()[:1])
    outcome = synchronize_public_workbook(downloader=FakeDownloader(collapsed), dry_run=True)

    assert outcome.result != "imported"
    assert _current().canonical_event_count == 9
