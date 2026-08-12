"""Model guarantees and the quality rules, checked directly.

The quality functions take plain values, so most of this file needs no database
at all — which is the point of keeping them free of model instances.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.membership.models import (
    InternalMembershipObservation,
    InternalObservationImmutable,
    InternalSourceKind,
    MembershipCountObservation,
    MembershipDataIssue,
    MembershipMetricConflict,
    MembershipMonthlyNewMemberValue,
    MonthlyValueStatus,
    QualityStatus,
)
from apps.membership.quality import (
    MetricFacts,
    PreferenceCandidate,
    assess,
    choose_preferred,
    collection_is_consistent,
    computed_collection_pct,
    impossible_metrics,
)

from .package_factory import SNAP_A_DIRECT

# --------------------------------------------------------------------------
# Quality rules, no database
# --------------------------------------------------------------------------


def test_paid_over_total_withholds_both_figures():
    withheld = impossible_metrics(MetricFacts(total_members=100, paid_members=101))

    assert withheld == frozenset({"total_members", "paid_members"})


def test_equal_paid_and_total_is_possible():
    assert impossible_metrics(MetricFacts(total_members=100, paid_members=100)) == frozenset()


def test_a_missing_figure_is_not_an_impossible_one():
    assert impossible_metrics(MetricFacts(total_members=100)) == frozenset()


def test_collection_percentage_is_computed_from_the_reported_amounts():
    assert computed_collection_pct(Decimal("525000"), Decimal("500000")) == Decimal("105.00")


def test_a_zero_budget_yields_no_percentage_rather_than_an_invented_one():
    assert computed_collection_pct(Decimal("1000"), Decimal("0")) is None


def test_collection_over_100_is_consistent_when_the_amounts_say_so():
    """Revenue can exceed a budget, and sixty real historical rows do."""
    assert collection_is_consistent(Decimal("105.00"), Decimal("525000"), Decimal("500000")) is True


def test_an_unverifiable_percentage_is_not_an_inconsistent_one():
    assert collection_is_consistent(Decimal("105.00"), None, None) is None


def test_an_inconsistent_percentage_is_detected():
    assert collection_is_consistent(Decimal("42.00"), Decimal("525000"), Decimal("500000")) is False


def test_rounding_within_tolerance_is_still_consistent():
    assert collection_is_consistent(Decimal("105.2"), Decimal("525000"), Decimal("500000")) is True


def test_assessment_of_a_clean_row_is_verified():
    outcome = assess(MetricFacts(total_members=100, paid_members=90))

    assert outcome.quality_status == QualityStatus.VERIFIED
    assert outcome.withheld_metrics == frozenset()


def test_assessment_withholds_only_the_conflicted_metric():
    outcome = assess(
        MetricFacts(total_members=100, paid_members=90, new_members_ytd=5),
        conflicted_metrics={"total_members"},
    )

    assert outcome.quality_status == QualityStatus.CONFLICTED
    assert outcome.withheld_metrics == frozenset({"total_members"})
    assert outcome.allows("paid_members") is True
    assert outcome.allows("new_members_ytd") is True


def test_an_impossible_pair_is_review_required():
    outcome = assess(MetricFacts(total_members=100, paid_members=101))

    assert outcome.quality_status == QualityStatus.REVIEW_REQUIRED
    assert outcome.allows("total_members") is False


def test_an_inconsistent_percentage_withholds_only_the_percentage():
    outcome = assess(
        MetricFacts(
            membership_fees_received_eur=Decimal("525000"),
            membership_fee_budget_eur=Decimal("500000"),
            membership_fee_collection_pct_reported=Decimal("42"),
        )
    )

    assert outcome.withheld_metrics == frozenset({"membership_fee_collection_pct_reported"})
    assert outcome.allows("membership_fees_received_eur") is True
    assert outcome.allows("membership_fee_budget_eur") is True


def test_assessment_is_deterministic():
    facts = MetricFacts(total_members=100, paid_members=101)

    assert assess(facts) == assess(facts)
    assert assess(facts).warning_codes == ("paid_exceeds_total",)


def test_manual_evidence_outranks_every_extraction():
    winner = choose_preferred(
        [
            PreferenceCandidate(
                "high-direct", InternalSourceKind.MERGED_SAME_DOCUMENT, "high", "verified", 1
            ),
            PreferenceCandidate(
                "manual", InternalSourceKind.MANUAL, "manual_verified", "verified", 2
            ),
        ]
    )

    assert winner.key == "manual"


def test_direct_evidence_outranks_a_more_confident_comparison():
    winner = choose_preferred(
        [
            PreferenceCandidate(
                "comparison", InternalSourceKind.REPORTED_COMPARISON, "high", "verified", 1
            ),
            PreferenceCandidate(
                "direct", InternalSourceKind.MERGED_SAME_DOCUMENT, "low", "verified", 2
            ),
        ]
    )

    assert winner.key == "direct"


def test_a_conflicted_row_can_still_be_preferred():
    """Refusing to prefer it would drop every uncontested number it carries."""
    winner = choose_preferred(
        [
            PreferenceCandidate(
                "direct", InternalSourceKind.MERGED_SAME_DOCUMENT, "high", "conflicted", 1
            ),
            PreferenceCandidate(
                "comparison", InternalSourceKind.REPORTED_COMPARISON, "high", "verified", 2
            ),
        ]
    )

    assert winner.key == "direct"


def test_no_candidates_yields_no_winner():
    assert choose_preferred([]) is None


# --------------------------------------------------------------------------
# Model guarantees
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_published_observation_refuses_every_fact_change(imported_package):
    observation = InternalMembershipObservation.objects.get(external_snapshot_id=SNAP_A_DIRECT)
    observation.total_members = 1

    with pytest.raises(InternalObservationImmutable):
        observation.save()


@pytest.mark.django_db
def test_only_the_two_state_fields_may_move(imported_package):
    observation = InternalMembershipObservation.objects.get(external_snapshot_id=SNAP_A_DIRECT)

    observation.is_preferred_for_date = False
    observation.save(update_fields=["is_preferred_for_date"])
    observation.quality_status = QualityStatus.SUPERSEDED
    observation.save(update_fields=["quality_status"])

    observation.refresh_from_db()
    assert observation.quality_status == QualityStatus.SUPERSEDED
    assert observation.total_members == 3200


@pytest.mark.django_db
def test_two_preferred_observations_on_one_date_are_refused(imported_package):
    comparison = InternalMembershipObservation.objects.get(
        source_kind=InternalSourceKind.REPORTED_COMPARISON
    )
    comparison.is_preferred_for_date = True

    with pytest.raises(IntegrityError), transaction.atomic():
        comparison.save(update_fields=["is_preferred_for_date"])


@pytest.mark.django_db
def test_a_conflict_month_may_not_be_given_a_value(imported_package, internal_source):
    with pytest.raises(IntegrityError), transaction.atomic():
        MembershipMonthlyNewMemberValue.objects.create(
            source=internal_source,
            import_run=imported_package.import_run,
            calendar_year=2023,
            calendar_month=5,
            new_members=0,
            value_status=MonthlyValueStatus.CONFLICT,
        )


@pytest.mark.django_db
def test_child_rows_are_unique_per_band_and_direction(imported_package):
    observation = InternalMembershipObservation.objects.get(external_snapshot_id=SNAP_A_DIRECT)
    movement = observation.size_movements.first()

    with pytest.raises(IntegrityError), transaction.atomic():
        observation.size_movements.create(
            direction=movement.direction,
            size_band_key=movement.size_band_key,
            member_count=1,
        )


@pytest.mark.django_db
def test_an_imported_child_row_cannot_be_edited(imported_package):
    movement = InternalMembershipObservation.objects.get(
        external_snapshot_id=SNAP_A_DIRECT
    ).size_movements.first()
    movement.member_count = 999

    with pytest.raises(InternalObservationImmutable):
        movement.save()


# --------------------------------------------------------------------------
# The resolution models: imported facts fixed, the resolution itself movable
# --------------------------------------------------------------------------
#
# Both models declared `MUTABLE_FIELDS` from the start but enforced nothing, so
# the contract was a comment. These tests hold the same line the sibling
# import models hold, from both directions.


@pytest.mark.django_db
class TestDataIssueImmutability:
    def test_the_import_creates_them(self, imported_package):
        """Creation is unaffected; only a later rewrite is refused."""
        assert MembershipDataIssue.objects.count() == 2

    def test_a_resolution_may_be_recorded(self, imported_package, staff_user):
        issue = MembershipDataIssue.objects.get(warning_code="collection_pct_over_100")

        issue.resolved = True
        issue.resolution_note = "Eelarve kontrollitud."
        issue.resolved_by = staff_user
        issue.resolved_at = timezone.now()
        issue.save(update_fields=["resolved", "resolution_note", "resolved_by", "resolved_at"])

        issue.refresh_from_db()
        assert issue.resolved is True
        assert issue.resolution_note == "Eelarve kontrollitud."

    def test_a_bare_save_is_refused(self, imported_package):
        """The commonest way to lose an imported fact."""
        issue = MembershipDataIssue.objects.first()
        issue.resolved = True

        with pytest.raises(InternalObservationImmutable):
            issue.save()

    def test_an_imported_field_cannot_be_rewritten(self, imported_package):
        issue = MembershipDataIssue.objects.first()
        issue.message = "Midagi muud."

        with pytest.raises(InternalObservationImmutable):
            issue.save(update_fields=["message"])

    def test_an_imported_field_cannot_ride_along_with_a_resolution(self, imported_package):
        """A permitted field does not license the ones beside it."""
        issue = MembershipDataIssue.objects.first()
        issue.resolved = True
        issue.severity = "error"

        with pytest.raises(InternalObservationImmutable):
            issue.save(update_fields=["resolved", "severity"])

    def test_the_stored_warning_survives_a_refused_write(self, imported_package):
        issue = MembershipDataIssue.objects.get(warning_code="collection_pct_over_100")
        original = issue.message
        issue.message = "Midagi muud."

        with pytest.raises(InternalObservationImmutable):
            issue.save(update_fields=["message"])

        issue.refresh_from_db()
        assert issue.message == original


@pytest.mark.django_db
class TestMetricConflictImmutability:
    def test_the_import_creates_them(self, imported_package):
        assert MembershipMetricConflict.objects.count() == 1

    def test_a_resolution_may_be_recorded(self, imported_package, staff_user):
        conflict = MembershipMetricConflict.objects.get()

        conflict.resolved = True
        conflict.resolution_note = "Valitud dokument A."
        conflict.resolved_by = staff_user
        conflict.resolved_at = timezone.now()
        conflict.save(update_fields=["resolved", "resolution_note", "resolved_by", "resolved_at"])

        conflict.refresh_from_db()
        assert conflict.resolved is True

    def test_a_bare_save_is_refused(self, imported_package):
        conflict = MembershipMetricConflict.objects.get()
        conflict.resolved = True

        with pytest.raises(InternalObservationImmutable):
            conflict.save()

    def test_the_disagreement_itself_cannot_be_rewritten(self, imported_package):
        """Editing this is how a conflict gets made to disappear."""
        conflict = MembershipMetricConflict.objects.get()
        conflict.values_summary = "3200 | 3200"

        with pytest.raises(InternalObservationImmutable):
            conflict.save(update_fields=["values_summary"])

    def test_the_metric_and_date_cannot_move(self, imported_package):
        conflict = MembershipMetricConflict.objects.get()
        conflict.metric = "paid_members"

        with pytest.raises(InternalObservationImmutable):
            conflict.save(update_fields=["metric"])


@pytest.mark.django_db
class TestTheAdminResolutionWorkflow:
    """The one legitimate writer must still work through the guard.

    `save_model` used to call a bare `obj.save()`, which the guard now refuses,
    so it names the resolution fields instead. Without these tests the guard
    would have silently broken the only workflow it is meant to permit.
    """

    def _admin_for(self, model):
        from django.contrib import admin as django_admin

        return django_admin.site._registry[model]

    def _post(self, rf, user):
        request = rf.post("/admin/")
        request.user = user
        return request

    def test_resolving_an_issue_stamps_the_resolver(self, imported_package, staff_user, rf):
        issue = MembershipDataIssue.objects.first()
        issue.resolved = True
        issue.resolution_note = "Vaadatud."

        self._admin_for(MembershipDataIssue).save_model(
            self._post(rf, staff_user), issue, form=None, change=True
        )

        issue.refresh_from_db()
        assert issue.resolved is True
        assert issue.resolved_by == staff_user
        assert issue.resolved_at is not None

    def test_resolving_an_issue_records_an_audit_event(self, imported_package, staff_user, rf):
        from apps.audit.models import AuditEvent
        from apps.membership.audit_actions import MembershipAudit

        issue = MembershipDataIssue.objects.first()
        issue.resolved = True

        self._admin_for(MembershipDataIssue).save_model(
            self._post(rf, staff_user), issue, form=None, change=True
        )

        assert AuditEvent.objects.filter(action=MembershipAudit.ISSUE_RESOLVED).exists()

    def test_unresolving_an_issue_clears_the_stamp(self, imported_package, staff_user, rf):
        issue = MembershipDataIssue.objects.first()
        admin_instance = self._admin_for(MembershipDataIssue)
        issue.resolved = True
        admin_instance.save_model(self._post(rf, staff_user), issue, form=None, change=True)

        issue.resolved = False
        admin_instance.save_model(self._post(rf, staff_user), issue, form=None, change=True)

        issue.refresh_from_db()
        assert issue.resolved is False
        assert issue.resolved_by is None
        assert issue.resolved_at is None

    def test_resolving_a_conflict_stamps_the_resolver(self, imported_package, staff_user, rf):
        conflict = MembershipMetricConflict.objects.get()
        conflict.resolved = True

        self._admin_for(MembershipMetricConflict).save_model(
            self._post(rf, staff_user), conflict, form=None, change=True
        )

        conflict.refresh_from_db()
        assert conflict.resolved is True
        assert conflict.resolved_by == staff_user

    def test_the_admin_cannot_add_or_delete_either_model(self, rf, staff_user):
        request = self._post(rf, staff_user)
        for model in (MembershipDataIssue, MembershipMetricConflict):
            admin_instance = self._admin_for(model)
            assert admin_instance.has_add_permission(request) is False
            assert admin_instance.has_delete_permission(request) is False

    def test_every_imported_field_is_read_only_in_the_admin(self):
        """The form cannot even offer what the model would refuse."""
        for model in (MembershipDataIssue, MembershipMetricConflict):
            admin_instance = self._admin_for(model)
            editable = set(admin_instance.fields) - set(admin_instance.readonly_fields)
            assert editable <= model.MUTABLE_FIELDS, (
                f"{model.__name__} offers {editable - model.MUTABLE_FIELDS} for editing, "
                "which the model refuses to save"
            )


@pytest.mark.django_db
def test_the_public_model_has_no_internal_fields():
    """An absent column cannot leak, and cannot be accidentally merged."""
    field_names = {field.name for field in MembershipCountObservation._meta.get_fields()}

    assert "paid_members" not in field_names
    assert "membership_fees_received_eur" not in field_names
    assert "quality_status" not in field_names
    assert "new_members_ytd" not in field_names


@pytest.mark.django_db
def test_no_model_in_this_app_can_hold_an_individual_member():
    """A standing guarantee, asserted rather than assumed."""
    from django.apps import apps

    forbidden = {"member_name", "registration_code", "member_url", "payment_status", "email"}
    for model in apps.get_app_config("membership").get_models():
        names = {field.name for field in model._meta.get_fields()}
        assert not (names & forbidden), f"{model.__name__} gained a member-level field"
