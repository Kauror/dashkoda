"""Re-importing a rebuilt package over the history it replaces.

The gap these tests close: `test_isolated_history_import` seeds a synthetic
history whose identifiers are invented (`seed_doc_0001`), so they never collide
with the ones the package carries. Production's history was written *by a
package*, so its identifiers are exactly the ones a rebuild reproduces —
document ids are derived from file content and are deliberately stable.

That difference is why superseding passed every test and then failed on the
first real attempt with

    UniqueViolation: membershipsourcedoc_unique_source_id
    Key (source_id, external_source_id)=(5, src_...) already exists

So these tests seed by *importing the package itself*, then import it again.
Whatever identity scheme the importer uses, it has to survive meeting its own
output.
"""

from __future__ import annotations

import pytest

from apps.membership.history_import import import_history_package
from apps.membership.models import (
    InternalMembershipObservation,
    MembershipDataIssue,
    MembershipHistoricalSourceDocument,
    MembershipMetricConflict,
    MembershipMonthlyNewMemberValue,
    QualityStatus,
)

from .package_factory import build_package

pytestmark = pytest.mark.django_db


@pytest.fixture
def history_written_by_a_package(tmp_path):
    """A history whose identifiers are the ones a package produces.

    This is what production actually contains, and what the synthetic seed in
    `test_isolated_history_import` does not reproduce.
    """
    import_history_package(
        build_package(tmp_path / "first.zip", schema_version="1.0"), dry_run=False
    )
    return None


def _rebuilt(tmp_path):
    """A different package that describes the same documents.

    A rebuild adds evidence; it does not rename the documents it already had.
    The source ids therefore repeat, which is the whole point.
    """
    return build_package(tmp_path / "rebuilt.zip", schema_version="2.0")


def test_superseding_over_a_package_written_history_succeeds(
    tmp_path, history_written_by_a_package
):
    """The regression. This is the exact failure seen in production."""
    result = import_history_package(_rebuilt(tmp_path), dry_run=False, supersede_previous=True)

    assert result.unchanged is False
    assert result.counts["superseded_observations"] == 3


def test_a_repeated_document_is_not_duplicated(tmp_path, history_written_by_a_package):
    """The same file is the same document, however many packages describe it."""
    before = set(
        MembershipHistoricalSourceDocument.objects.values_list("external_source_id", flat=True)
    )

    import_history_package(_rebuilt(tmp_path), dry_run=False, supersede_previous=True)

    ids = list(
        MembershipHistoricalSourceDocument.objects.values_list("external_source_id", flat=True)
    )
    assert len(ids) == len(set(ids)), "a document was written twice"
    assert before <= set(ids), "an existing document disappeared"


def test_every_old_observation_survives_with_its_values(tmp_path, history_written_by_a_package):
    before = {
        pk: total
        for pk, total in InternalMembershipObservation.objects.values_list("id", "total_members")
    }

    import_history_package(_rebuilt(tmp_path), dry_run=False, supersede_previous=True)

    for pk, total in before.items():
        row = InternalMembershipObservation.objects.get(pk=pk)
        assert row.total_members == total
        assert row.quality_status == QualityStatus.SUPERSEDED
        assert row.is_preferred_for_date is False


def test_exactly_one_preferred_observation_per_date_afterwards(
    tmp_path, history_written_by_a_package
):
    """The constraint the guard already handles; pinned so it stays handled."""
    import_history_package(_rebuilt(tmp_path), dry_run=False, supersede_previous=True)

    preferred = InternalMembershipObservation.objects.filter(is_preferred_for_date=True)
    dates = list(preferred.values_list("observation_date", flat=True))
    assert len(dates) == len(set(dates))


def test_exactly_one_current_value_per_month_afterwards(tmp_path, history_written_by_a_package):
    import_history_package(_rebuilt(tmp_path), dry_run=False, supersede_previous=True)

    current = MembershipMonthlyNewMemberValue.objects.filter(is_current_for_month=True)
    months = list(current.values_list("calendar_year", "calendar_month"))
    assert len(months) == len(set(months)), "a month has two current values"


def test_warnings_and_conflicts_do_not_collide(tmp_path, history_written_by_a_package):
    import_history_package(_rebuilt(tmp_path), dry_run=False, supersede_previous=True)

    warnings = [
        w for w in MembershipDataIssue.objects.values_list("external_warning_id", flat=True) if w
    ]
    assert len(warnings) == len(set(warnings))

    conflicts = list(MembershipMetricConflict.objects.values_list("observation_date", "metric"))
    assert len(conflicts) == len(set(conflicts))


def test_no_unique_constraint_in_the_app_survives_a_supersede(
    tmp_path, history_written_by_a_package
):
    """Every uniqueness rule at once, rather than one CI round each.

    Six constraints were found one failure at a time — source documents,
    observations, monthly values, warnings, conflicts, and then decision
    batches. Each fix revealed the next, because nothing asserted the whole set.

    This walks the app's models, imports a second package over the first, and
    checks every `UniqueConstraint` still holds. A seventh constraint added
    later is covered the day it is written, without anyone remembering to.
    """
    from django.apps import apps as django_apps
    from django.db.models import UniqueConstraint

    import_history_package(_rebuilt(tmp_path), dry_run=False, supersede_previous=True)

    checked = 0
    for model in django_apps.get_app_config("membership").get_models():
        for constraint in model._meta.constraints:
            if not isinstance(constraint, UniqueConstraint):
                continue
            if constraint.condition is not None:
                # A partial constraint only binds the rows it selects, and the
                # database is already enforcing it; re-deriving the predicate
                # here would be a second, drifting copy of it.
                continue
            fields = list(constraint.fields)
            # SQL treats NULLs as distinct in a unique constraint, so a nullable
            # column repeating `None` is not a duplicate. Python disagrees, and
            # comparing the tuples naively reports every row of a two-parent
            # table — `MembershipNewMemberSizeDistribution` has 560 rows whose
            # `period` is null — as a violation the database never had.
            rows = [row for row in model.objects.values_list(*fields) if None not in row]
            assert len(rows) == len(set(rows)), (
                f"{model.__name__}.{constraint.name} has duplicates after a supersede"
            )
            checked += 1

    assert checked >= 5, "expected to have checked the membership uniqueness rules"


def test_the_page_sees_one_generation_of_decisions_after_a_supersede(
    tmp_path, history_written_by_a_package
):
    """What the reader gets, not just what the tables hold.

    The uniqueness sweep passed while the page showed every board decision
    twice: the run-qualified key let both generations exist legitimately, and
    the selector excludes only superseded rows. Nothing asserted what a reader
    would actually see, so nothing caught it.
    """
    from apps.membership.internal_selectors import get_decision_batches

    import_history_package(_rebuilt(tmp_path), dry_run=False, supersede_previous=True)
    import_history_package(
        build_package(tmp_path / "third.zip", schema_version="2.0", readme=b"# third\n"),
        dry_run=False,
        supersede_previous=True,
    )

    drawn = get_decision_batches()
    seen = [(b.as_of_date, b.kind) for b in drawn]
    assert len(seen) == len(set(seen)), "a decision is drawn more than once"


def test_a_failed_supersede_leaves_the_history_whole(tmp_path, history_written_by_a_package):
    """A rebuild that cannot be written must not retire what is already there."""
    from apps.membership.history_import import MembershipHistoryImportError

    def corrupt(payloads):
        payloads["data/decision_batches.csv"] = b"batch_id,wrong\n"
        return payloads

    broken = build_package(tmp_path / "broken.zip", schema_version="2.0", mutate_payloads=corrupt)

    with pytest.raises(MembershipHistoryImportError):
        import_history_package(broken, dry_run=False, supersede_previous=True)

    assert not InternalMembershipObservation.objects.filter(
        quality_status=QualityStatus.SUPERSEDED
    ).exists()
