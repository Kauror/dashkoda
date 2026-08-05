"""Read-only admin inspection and the two scheduled commands.

The admin is where shadow evaluation happens, so the fields and filters a
reviewer needs are asserted here rather than left to be noticed missing during
the first real run. What is asserted just as firmly is the absence of every add,
edit, delete, approve, reject and override action: this phase measures the
matcher, it does not operate it.
"""

from __future__ import annotations

import json

import pytest
from django.contrib import admin
from django.urls import reverse

from apps.legal_work.admin import (
    CurrentTopicItemAdmin,
    LegalCurrentTopicMatchAdmin,
    LegalCurrentTopicMatchSnapshotAdmin,
)
from apps.legal_work.current_topic_match_sync import run_current_topic_matching
from apps.legal_work.current_topic_matching import MATCHER_VERSION
from apps.legal_work.models import (
    CurrentTopicFeedState,
    CurrentTopicItem,
    CurrentTopicSnapshot,
    LegalCurrentTopicMatch,
    LegalCurrentTopicMatchSnapshot,
)

from .current_topic_factory import DETAIL_PREFIX, LISTING_PATH, FakeSite, card, detail, listing

pytestmark = pytest.mark.django_db

NEW_MODELS = (
    CurrentTopicSnapshot,
    CurrentTopicItem,
    CurrentTopicFeedState,
    LegalCurrentTopicMatchSnapshot,
    LegalCurrentTopicMatch,
)


def catalogue(*slugs: str) -> FakeSite:
    pages = {LISTING_PATH: listing(*(card(slug, f"Teema {slug}") for slug in slugs))}
    for slug in slugs:
        pages[f"{DETAIL_PREFIX}{slug}"] = detail(title=f"Teema {slug}")
    return FakeSite(pages)


@pytest.fixture
def matched(imported_snapshot, publish_current_topics):
    publish_current_topics(catalogue("alpha", "beeta"))
    run_current_topic_matching()
    return LegalCurrentTopicMatchSnapshot.objects.get(is_current=True)


# -- admin: registration and read-only ------------------------------------


@pytest.mark.parametrize("model", NEW_MODELS)
def test_every_new_model_is_registered(model):
    assert model in admin.site._registry


@pytest.mark.parametrize("model", NEW_MODELS)
def test_no_new_model_can_be_added_changed_or_deleted(model, rf, superuser):
    model_admin = admin.site._registry[model]
    request = rf.get("/admin/")
    request.user = superuser

    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request) is False
    assert model_admin.has_delete_permission(request) is False
    assert set(model_admin.get_readonly_fields(request)) == {
        field.name for field in model._meta.fields
    }


@pytest.mark.parametrize("model", NEW_MODELS)
def test_no_new_model_offers_an_approve_or_override_action(model, rf, superuser):
    model_admin = admin.site._registry[model]
    request = rf.get("/admin/")
    request.user = superuser

    actions = model_admin.get_actions(request)

    assert "delete_selected" not in actions
    assert not any(
        word in name for name in actions for word in ("approve", "reject", "override", "link")
    )


def test_the_admin_requires_staff(client, matched):
    url = reverse("admin:legal_work_legalcurrenttopicmatch_changelist")

    response = client.get(url)

    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]


def test_staff_can_inspect_the_match_list(client, superuser, matched):
    client.force_login(superuser)

    response = client.get(reverse("admin:legal_work_legalcurrenttopicmatch_changelist"))

    assert response.status_code == 200


def test_staff_can_inspect_the_catalogue_list(client, superuser, matched):
    client.force_login(superuser)

    response = client.get(reverse("admin:legal_work_currenttopicitem_changelist"))

    assert response.status_code == 200


# -- admin: the fields a reviewer needs ------------------------------------


def test_the_catalogue_list_shows_what_a_reviewer_has_to_read():
    displayed = set(CurrentTopicItemAdmin.list_display)

    assert {
        "title",
        "canonical_url",
        "published_date",
        "feedback_deadline",
        "named_organization",
        "summary_excerpt",
        "snapshot",
    } <= displayed


def test_the_match_list_shows_every_field_calibration_needs():
    displayed = set(LegalCurrentTopicMatchAdmin.list_display)

    assert {
        "legal_record_id",
        "legal_topic",
        "candidate_title",
        "candidate_link",
        "decision",
        "score",
        "runner_up_score",
        "score_margin",
        "evidence_summary",
        "matcher_version",
    } <= displayed


def test_the_match_list_can_be_filtered_the_ways_calibration_needs():
    filters = LegalCurrentTopicMatchAdmin.list_filter
    names = {item if isinstance(item, str) else item.parameter_name for item in filters}

    assert {
        "decision",
        "score_band",
        "evidence",
        "snapshot__matcher_version",
        "snapshot__legal_snapshot",
        "snapshot__current_topic_snapshot",
    } <= names


def test_the_snapshot_list_shows_the_run_totals():
    displayed = set(LegalCurrentTopicMatchSnapshotAdmin.list_display)

    assert {
        "matcher_version",
        "legal_item_count",
        "matched_count",
        "ambiguous_count",
        "unmatched_count",
    } <= displayed


def test_the_score_band_filter_selects_a_real_range(matched, rf, superuser):
    from apps.legal_work.admin import DecisionScoreBandFilter

    request = rf.get("/admin/", {"score_band": "low"})
    request.user = superuser
    filtered = DecisionScoreBandFilter(
        request, {"score_band": ["low"]}, LegalCurrentTopicMatch, LegalCurrentTopicMatchAdmin
    ).queryset(request, LegalCurrentTopicMatch.objects.all())

    assert all(match.score < 38 for match in filtered)


def test_the_evidence_filter_selects_rows_carrying_the_code(matched, rf, superuser):
    from apps.legal_work.admin import EvidenceCodeFilter

    request = rf.get("/admin/", {"evidence": "no-plausible-candidate"})
    request.user = superuser
    filtered = EvidenceCodeFilter(
        request,
        {"evidence": ["no-plausible-candidate"]},
        LegalCurrentTopicMatch,
        LegalCurrentTopicMatchAdmin,
    ).queryset(request, LegalCurrentTopicMatch.objects.all())

    assert all("no-plausible-candidate" in match.evidence_codes for match in filtered)


# -- the collection command -------------------------------------------------


def test_the_sync_command_reports_imported_then_unchanged(monkeypatch):
    from django.core.management import call_command

    monkeypatch.setattr("apps.legal_work.current_topics.fetch", catalogue("alpha"))

    first = _capture(call_command, "sync_legal_current_topics", "--json")
    second = _capture(call_command, "sync_legal_current_topics", "--json")

    assert json.loads(first)["result"] == "imported"
    assert json.loads(second)["result"] == "unchanged"


def test_the_sync_command_json_carries_no_source_content(monkeypatch):
    from django.core.management import call_command

    monkeypatch.setattr("apps.legal_work.current_topics.fetch", catalogue("alpha"))

    payload = json.loads(_capture(call_command, "sync_legal_current_topics", "--json"))

    assert set(payload) == {"result", "detail", "dry_run", "snapshot_id", "item_count"}
    serialised = json.dumps(payload, ensure_ascii=False)
    assert "http" not in serialised
    assert "Teema" not in serialised


def test_the_sync_command_dry_run_publishes_nothing(monkeypatch):
    from django.core.management import call_command

    monkeypatch.setattr("apps.legal_work.current_topics.fetch", catalogue("alpha"))

    payload = json.loads(_capture(call_command, "sync_legal_current_topics", "--json", "--dry-run"))

    assert payload["dry_run"] is True
    assert not CurrentTopicSnapshot.objects.exists()


def test_the_sync_command_exits_one_on_failure(monkeypatch):
    from django.core.management import call_command

    from apps.core.public_http import PublicFetchError

    monkeypatch.setattr(
        "apps.legal_work.current_topics.fetch",
        FakeSite({}, errors={LISTING_PATH: PublicFetchError("Allikas vastas koodiga 503.")}),
    )

    with pytest.raises(SystemExit) as exit_info:
        call_command("sync_legal_current_topics", "--json")

    assert exit_info.value.code == 1


def test_the_sync_command_exits_three_when_locked(monkeypatch):
    from django.core.management import call_command

    from apps.core.feeds import FeedLocked

    def locked(name):
        raise FeedLocked("Allika sünkroonimine juba käib.")

    monkeypatch.setattr(
        "apps.legal_work.management.commands.sync_legal_current_topics.advisory_lock", locked
    )

    with pytest.raises(SystemExit) as exit_info:
        call_command("sync_legal_current_topics")

    assert exit_info.value.code == 3


def test_the_sync_command_accepts_no_url_argument():
    from apps.legal_work.management.commands.sync_legal_current_topics import Command

    parser = Command().create_parser("manage.py", "sync_legal_current_topics")
    options = {action.dest for action in parser._actions}

    assert "url" not in options
    assert {"dry_run", "as_json"} <= options


# -- the matching command ---------------------------------------------------


def test_the_match_command_reports_imported_then_unchanged(
    imported_snapshot, publish_current_topics
):
    from django.core.management import call_command

    publish_current_topics(catalogue("alpha"))

    first = json.loads(_capture(call_command, "match_legal_current_topics", "--json"))
    second = json.loads(_capture(call_command, "match_legal_current_topics", "--json"))

    assert first["result"] == "imported"
    assert second["result"] == "unchanged"
    assert first["matcher_version"] == MATCHER_VERSION


def test_the_match_command_json_carries_only_aggregates(imported_snapshot, publish_current_topics):
    from django.core.management import call_command

    publish_current_topics(catalogue("alpha"))

    payload = json.loads(_capture(call_command, "match_legal_current_topics", "--json"))

    assert set(payload) == {
        "result",
        "detail",
        "dry_run",
        "snapshot_id",
        "legal_item_count",
        "current_topic_count",
        "matched_count",
        "ambiguous_count",
        "unmatched_count",
        "matcher_version",
    }
    serialised = json.dumps(payload, ensure_ascii=False)
    assert "http" not in serialised
    assert "Teema" not in serialised
    assert "Sünteetiline" not in serialised


def test_the_match_command_dry_run_publishes_nothing(imported_snapshot, publish_current_topics):
    from django.core.management import call_command

    publish_current_topics(catalogue("alpha"))

    payload = json.loads(
        _capture(call_command, "match_legal_current_topics", "--json", "--dry-run")
    )

    assert payload["dry_run"] is True
    assert not LegalCurrentTopicMatchSnapshot.objects.exists()


def test_the_match_command_exits_one_without_inputs(db):
    from django.core.management import call_command

    with pytest.raises(SystemExit) as exit_info:
        call_command("match_legal_current_topics", "--json")

    assert exit_info.value.code == 1


def test_the_match_command_exits_three_when_locked(monkeypatch, db):
    from django.core.management import call_command

    from apps.core.feeds import FeedLocked

    def locked(name):
        raise FeedLocked("Sobitamine juba käib.")

    monkeypatch.setattr(
        "apps.legal_work.management.commands.match_legal_current_topics.advisory_lock", locked
    )

    with pytest.raises(SystemExit) as exit_info:
        call_command("match_legal_current_topics")

    assert exit_info.value.code == 3


def test_the_two_commands_take_different_locks():
    from apps.core.feeds import advisory_lock_key
    from apps.legal_work.current_topic_match_sync import LOCK_NAME as MATCH_LOCK
    from apps.legal_work.current_topic_sync import LOCK_NAME as SYNC_LOCK
    from apps.legal_work.sync import ADVISORY_LOCK_NAMESPACE as WORKBOOK_LOCK

    keys = {advisory_lock_key(name) for name in (MATCH_LOCK, SYNC_LOCK, WORKBOOK_LOCK)}

    assert len(keys) == 3


def _capture(call, *args, **kwargs) -> str:
    """Run a management command and return everything it wrote to stdout."""
    import io

    buffer = io.StringIO()
    call(*args, stdout=buffer, **kwargs)
    return buffer.getvalue().strip()
