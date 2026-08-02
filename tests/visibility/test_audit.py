"""What the audit trail records about a manual submission, and what it must not.

These counts are aggregate business figures rather than personal data, so they
are safe to keep. The note somebody typed, the raw form payload and anything
resembling a credential are not, and the existing redaction rules still apply.
"""

from __future__ import annotations

import pytest

from apps.audit.models import AuditAction, AuditEvent
from apps.visibility.models import VisibilityEntryBatch, VisibilityObservation

from .conftest import NEW_URL, confirm, form_data

pytestmark = pytest.mark.django_db


def _summaries(action) -> list[dict]:
    return [event.change_summary for event in AuditEvent.objects.filter(action=action)]


def test_a_published_batch_is_recorded(submit):
    submit(facebook_followers=4200)

    summary = _summaries(AuditAction.VISIBILITY_MANUAL_BATCH_PUBLISHED)[0]
    batch = VisibilityEntryBatch.objects.get()

    assert summary["batch_id"] == batch.pk
    assert summary["observation_date"] == batch.observation_date.isoformat()
    assert summary["observation_count"] == 1
    assert summary["metrics"] == {"facebook_followers": 4200}
    assert summary["sources"] == ["manual-facebook-followers"]


def test_every_observation_is_recorded_individually(submit):
    submit(facebook_followers=4200, linkedin_followers=2500)

    summaries = _summaries(AuditAction.VISIBILITY_OBSERVATION_PUBLISHED)

    assert len(summaries) == 2
    assert {summary["metric"] for summary in summaries} == {
        "facebook_followers",
        "linkedin_followers",
    }
    assert all(summary["collection_method"] == "manual" for summary in summaries)


def test_a_supersession_is_recorded_with_both_values(submit):
    submit(facebook_followers=4200)
    submit(facebook_followers=4250)

    summary = _summaries(AuditAction.VISIBILITY_OBSERVATION_SUPERSEDED)[0]

    assert summary["metric"] == "facebook_followers"
    assert summary["superseded_value"] == 4200
    assert summary["replacement_value"] == 4250
    assert summary["superseded_observation_id"] != summary["replacement_observation_id"]


def test_a_first_observation_records_no_supersession(submit):
    submit(facebook_followers=4200)

    assert not AuditEvent.objects.filter(
        action=AuditAction.VISIBILITY_OBSERVATION_SUPERSEDED
    ).exists()
    summary = _summaries(AuditAction.VISIBILITY_OBSERVATION_PUBLISHED)[0]
    assert summary["supersedes_observation_id"] is None


def test_one_correlation_id_covers_a_whole_submission(submit):
    submit(facebook_followers=4200, linkedin_followers=2500, newsletter_eteataja=1200)

    batch = VisibilityEntryBatch.objects.get()
    correlation_ids = set(
        AuditEvent.objects.filter(
            action__in=[
                AuditAction.VISIBILITY_MANUAL_BATCH_PUBLISHED,
                AuditAction.VISIBILITY_OBSERVATION_PUBLISHED,
            ]
        ).values_list("correlation_id", flat=True)
    )

    assert correlation_ids == {batch.correlation_id}


def test_the_note_never_reaches_the_audit_trail(staff_client):
    staff_client.post(
        NEW_URL,
        confirm(form_data(facebook_followers=4200, note="Sisemine kommentaar juhatusele.")),
    )

    trail = " ".join(str(event.change_summary) for event in AuditEvent.objects.all())

    assert "Sisemine kommentaar" not in trail
    # It is stored where it belongs.
    assert VisibilityEntryBatch.objects.get().note == "Sisemine kommentaar juhatusele."


def test_no_form_payload_or_session_value_reaches_the_audit_trail(staff_client):
    staff_client.post(NEW_URL, confirm(form_data(facebook_followers=4200)))

    trail = " ".join(str(event.change_summary) for event in AuditEvent.objects.all()).lower()

    for forbidden in ("csrf", "sessionid", "password", "token", "metric_facebook", "action"):
        assert forbidden not in trail


def test_no_profile_url_reaches_the_audit_trail(submit):
    submit(facebook_followers=4200)

    trail = " ".join(str(event.change_summary) for event in AuditEvent.objects.all())

    assert "facebook.com" not in trail
    assert "https://" not in trail


def test_the_recorded_summary_is_aggregate_only(submit):
    submit(facebook_followers=4200)

    summary = _summaries(AuditAction.VISIBILITY_OBSERVATION_PUBLISHED)[0]

    assert set(summary) == {
        "source",
        "metric",
        "value",
        "observation_date",
        "batch_id",
        "collection_method",
        "supersedes_observation_id",
    }


def test_the_content_hash_survives_redaction(submit):
    """A checksum is a wanted, non-secret fact and is exempt from the hash rule."""
    submit(facebook_followers=4200)

    summary = _summaries(AuditAction.VISIBILITY_MANUAL_BATCH_PUBLISHED)[0]

    assert summary["content_sha256"] == VisibilityEntryBatch.objects.get().content_hash
    assert summary["content_sha256"] != "[redacted]"


def test_an_audit_event_cannot_be_rewritten_or_removed(submit):
    from apps.audit.models import AuditEventImmutable

    submit(facebook_followers=4200)
    event = AuditEvent.objects.filter(action=AuditAction.VISIBILITY_MANUAL_BATCH_PUBLISHED).get()

    with pytest.raises(AuditEventImmutable):
        event.delete()
    assert VisibilityObservation.objects.count() == 1
