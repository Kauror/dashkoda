"""The staff-only manual entry and correction workflow."""

from __future__ import annotations

from datetime import date

import pytest
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditAction, AuditEvent
from apps.membership.models import (
    InternalMembershipObservation,
    InternalObservationImmutable,
    InternalSourceKind,
    MembershipMonthlyNewMemberValue,
    MonthlyValueStatus,
    QualityStatus,
)

pytestmark = pytest.mark.django_db

NEW_URL = "/admin/membership/internal-report/new/"


def form_data(**overrides) -> dict:
    """A minimal valid submission. Overrides replace or add single fields."""
    data = {
        "observation_date": "2026-02-05",
        "reported_year": "2026",
        "document_title": "Liikmeskonnast 2026",
        "source_note": "",
        "supersedes": "",
        "total_members": "3400",
        "paid_members": "3200",
        "membership_fees_received_eur": "550000.00",
        "membership_fee_budget_eur": "500000.00",
        "membership_fee_collection_pct_reported": "110.00",
        "new_members_ytd": "20",
        "suspended_members": "4",
        "removed_members_ytd": "10",
        "monthly_year": "2026",
        "month_1": "12",
        "month_2": "8",
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value is not None}


def preview(data: dict) -> dict:
    return {**data, "action": "preview"}


def confirm(data: dict) -> dict:
    return {**data, "action": "confirm"}


# --------------------------------------------------------------------------
# Access
# --------------------------------------------------------------------------


def test_staff_can_open_the_form(staff_client):
    response = staff_client.get(NEW_URL)

    assert response.status_code == 200
    assert "Lisa liikmeskonna aruanne" in response.content.decode()


def test_viewer_pin_alone_cannot_reach_the_form(viewer_client):
    """A PIN gets you into the dashboard, never into data entry."""
    response = viewer_client.get(NEW_URL)

    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]


def test_anonymous_visitor_is_sent_to_the_viewer_login(client):
    response = client.get(NEW_URL)

    assert response.status_code == 302
    assert reverse("viewer-login") in response["Location"]


def test_viewer_cannot_publish_by_posting_directly(viewer_client):
    response = viewer_client.post(NEW_URL, confirm(form_data()))

    assert response.status_code == 302
    assert InternalMembershipObservation.objects.count() == 0


def test_csrf_is_enforced(staff_user, authenticate_viewer):
    enforcing = Client(enforce_csrf_checks=True)
    authenticate_viewer(enforcing)
    enforcing.force_login(staff_user)

    response = enforcing.post(NEW_URL, confirm(form_data()))

    assert response.status_code == 403
    assert InternalMembershipObservation.objects.count() == 0


# --------------------------------------------------------------------------
# Preview and publication
# --------------------------------------------------------------------------


def test_preview_saves_nothing(staff_client):
    response = staff_client.post(NEW_URL, preview(form_data()))

    assert response.status_code == 200
    assert InternalMembershipObservation.objects.count() == 0
    assert MembershipMonthlyNewMemberValue.objects.count() == 0


def test_preview_shows_the_calculated_values(staff_client):
    response = staff_client.post(NEW_URL, preview(form_data()))
    body = response.content.decode()

    # 550 000 of a 500 000 budget is 110 %, which matches what was reported.
    assert "110.00" in body
    assert "Kontroll" in body


def test_confirmation_publishes_atomically(staff_client):
    response = staff_client.post(NEW_URL, confirm(form_data()), follow=True)

    assert response.status_code == 200
    observation = InternalMembershipObservation.objects.get()
    assert observation.source_kind == InternalSourceKind.MANUAL
    assert observation.observation_date == date(2026, 2, 5)
    assert observation.total_members == 3400
    assert observation.is_preferred_for_date is True
    assert observation.published_at is not None
    assert MembershipMonthlyNewMemberValue.objects.count() == 2


def test_publication_redirects_rather_than_re_rendering(staff_client):
    response = staff_client.post(NEW_URL, confirm(form_data()))

    assert response.status_code == 302
    assert response["Location"].startswith("/admin/membership/internal-report/")


def test_double_submit_does_not_create_a_second_record(staff_client):
    staff_client.post(NEW_URL, confirm(form_data()))
    staff_client.post(NEW_URL, confirm(form_data()))

    assert InternalMembershipObservation.objects.count() == 1


def test_partial_entry_is_accepted(staff_client):
    """Older reports omit figures. A form that demanded them would invite
    someone to type a number nobody reported."""
    sparse = {
        "observation_date": "2026-03-05",
        "total_members": "3405",
    }
    staff_client.post(NEW_URL, confirm(sparse))

    observation = InternalMembershipObservation.objects.get()
    assert observation.total_members == 3405
    assert observation.paid_members is None
    assert observation.membership_fees_received_eur is None


def test_audit_event_records_aggregates_and_not_the_note(staff_client):
    staff_client.post(NEW_URL, confirm(form_data(source_note="Sisemine kommentaar juhatusele.")))

    event = AuditEvent.objects.get(action=AuditAction.MEMBERSHIP_MANUAL_OBSERVATION_CREATED)
    summary = str(event.change_summary)

    assert "2026-02-05" in summary
    assert "Sisemine kommentaar" not in summary
    assert "csrf" not in summary.lower()


def test_the_note_itself_is_stored_on_the_observation(staff_client):
    staff_client.post(NEW_URL, confirm(form_data(source_note="Juhatuse märkus.")))

    assert InternalMembershipObservation.objects.get().source_note == "Juhatuse märkus."


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def test_paid_members_may_not_exceed_total(staff_client):
    response = staff_client.post(NEW_URL, confirm(form_data(paid_members="9999")))

    assert response.status_code == 200
    assert "rohkem kui liikmeid kokku" in response.content.decode()
    assert InternalMembershipObservation.objects.count() == 0


def test_a_future_month_is_refused(staff_client):
    """The observation is dated February; a December figure cannot exist yet."""
    response = staff_client.post(NEW_URL, confirm(form_data(month_12="5")))

    assert response.status_code == 200
    assert "hilisemat kuud" in response.content.decode()
    assert InternalMembershipObservation.objects.count() == 0


def test_negative_counts_are_refused(staff_client):
    response = staff_client.post(NEW_URL, confirm(form_data(total_members="-5")))

    assert response.status_code == 200
    assert InternalMembershipObservation.objects.count() == 0


def test_blank_and_zero_months_stay_distinct(staff_client):
    staff_client.post(NEW_URL, confirm(form_data(month_1="0", month_2="")))

    values = MembershipMonthlyNewMemberValue.objects.all()
    assert values.count() == 1
    stored = values.get()
    assert stored.calendar_month == 1
    assert stored.new_members == 0


def test_size_totals_are_only_checked_when_marked_complete(staff_client):
    """A partial distribution is normal and must not be rejected."""
    partial = form_data(joined_employees_1_4="3")
    response = staff_client.post(NEW_URL, confirm(partial))

    assert response.status_code == 302
    assert InternalMembershipObservation.objects.get().size_movements.count() == 1


def test_complete_size_table_must_add_up(staff_client):
    mismatched = form_data(joined_employees_1_4="3", size_table_complete="on")
    response = staff_client.post(NEW_URL, confirm(mismatched))

    assert response.status_code == 200
    assert "ei klapi uute liikmete arvuga" in response.content.decode()
    assert InternalMembershipObservation.objects.count() == 0


def test_complete_reason_table_must_add_up(staff_client):
    mismatched = form_data(
        reason_dissolved_bankrupt_merged_inactive_missing="3",
        reasons_complete="on",
    )
    response = staff_client.post(NEW_URL, confirm(mismatched))

    assert response.status_code == 200
    assert "põhjuste summa ei klapi" in response.content.decode()


def test_percentage_mismatch_warns_without_blocking(staff_client):
    """A warning is a warning. It asks for a second look, not for a retype."""
    response = staff_client.post(
        NEW_URL, preview(form_data(membership_fee_collection_pct_reported="42.00"))
    )
    body = response.content.decode()

    assert "erineb laekunud summa ja eelarve suhtest" in body
    assert "Kinnita ja salvesta" in body


def test_collection_over_100_is_not_treated_as_an_error(staff_client):
    staff_client.post(NEW_URL, confirm(form_data()))

    observation = InternalMembershipObservation.objects.get()
    assert observation.membership_fee_collection_pct_reported == 110
    assert observation.quality_status == QualityStatus.VERIFIED


def test_other_removal_reason_keeps_its_own_label(staff_client):
    staff_client.post(
        NEW_URL,
        confirm(form_data(other_reason_label="Ühines teise kojaga", other_reason_count="2")),
    )

    reason = InternalMembershipObservation.objects.get().removal_reasons.get()
    assert reason.reason_key == "other"
    assert reason.reason_label_raw == "Ühines teise kojaga"


# --------------------------------------------------------------------------
# Corrections
# --------------------------------------------------------------------------


def _publish(staff_client, **overrides):
    staff_client.post(NEW_URL, confirm(form_data(**overrides)))
    return InternalMembershipObservation.objects.order_by("-id").first()


def test_correction_creates_a_revision_and_retires_the_original(staff_client):
    original = _publish(staff_client)

    correction_url = f"/admin/membership/internal-report/{original.pk}/correct/"
    response = staff_client.get(correction_url)
    assert response.status_code == 200

    staff_client.post(
        correction_url,
        confirm(form_data(total_members="3410", supersedes=str(original.pk))),
    )

    original.refresh_from_db()
    revision = InternalMembershipObservation.objects.exclude(pk=original.pk).get()

    assert revision.total_members == 3410
    assert revision.supersedes_id == original.pk
    assert revision.is_preferred_for_date is True
    # The original keeps every reported number it ever had.
    assert original.total_members == 3400
    assert original.quality_status == QualityStatus.SUPERSEDED
    assert original.is_preferred_for_date is False


def test_superseding_is_audited(staff_client):
    original = _publish(staff_client)
    staff_client.post(
        f"/admin/membership/internal-report/{original.pk}/correct/",
        confirm(form_data(total_members="3410", supersedes=str(original.pk))),
    )

    assert AuditEvent.objects.filter(
        action=AuditAction.MEMBERSHIP_MANUAL_OBSERVATION_SUPERSEDED
    ).exists()


def test_a_superseded_observation_cannot_be_corrected_again(staff_client):
    original = _publish(staff_client)
    staff_client.post(
        f"/admin/membership/internal-report/{original.pk}/correct/",
        confirm(form_data(total_members="3410", supersedes=str(original.pk))),
    )

    response = staff_client.get(f"/admin/membership/internal-report/{original.pk}/correct/")
    assert response.status_code == 404


def test_a_published_observation_refuses_to_be_rewritten(staff_client):
    observation = _publish(staff_client)
    observation.total_members = 1

    with pytest.raises(InternalObservationImmutable):
        observation.save()


def test_correcting_a_month_supersedes_the_previous_value(staff_client):
    original = _publish(staff_client)
    staff_client.post(
        f"/admin/membership/internal-report/{original.pk}/correct/",
        confirm(form_data(month_1="14", supersedes=str(original.pk))),
    )

    january = MembershipMonthlyNewMemberValue.objects.filter(
        calendar_year=2026, calendar_month=1
    ).order_by("id")

    assert january.count() == 2
    assert january.first().value_status == MonthlyValueStatus.SUPERSEDED
    assert january.first().is_current_for_month is False
    current = january.last()
    assert current.new_members == 14
    assert current.is_current_for_month is True


def test_correction_to_a_different_date_needs_explicit_confirmation(staff_client):
    original = _publish(staff_client)
    moved = form_data(observation_date="2026-02-06", supersedes=str(original.pk))

    blocked = staff_client.post(
        f"/admin/membership/internal-report/{original.pk}/correct/", confirm(moved)
    )
    assert blocked.status_code == 200
    assert "Kinnita kuupäeva muutmine" in blocked.content.decode()

    allowed = staff_client.post(
        f"/admin/membership/internal-report/{original.pk}/correct/",
        confirm({**moved, "confirm_date_change": "on"}),
    )
    assert allowed.status_code == 302


def test_detail_page_offers_no_way_to_edit_the_record(staff_client):
    """The admin chrome has its own logout form, so the check is specific:
    nothing on this page posts to a membership route."""
    observation = _publish(staff_client)

    response = staff_client.get(f"/admin/membership/internal-report/{observation.pk}/")
    body = response.content.decode()

    assert response.status_code == 200
    assert 'action="/admin/membership/' not in body
    assert "Loo parandatud versioon" in body


def test_detail_page_rejects_a_post(staff_client):
    observation = _publish(staff_client)

    response = staff_client.post(f"/admin/membership/internal-report/{observation.pk}/", {})

    assert response.status_code == 405
