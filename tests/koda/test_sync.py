"""Publication, idempotency and failure containment for the three public feeds.

Each source is exercised through its own synchronise function with a synthetic
collector. No HTTP happens anywhere in this module.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.audit.models import AuditEvent
from apps.core.feeds import FeedResult
from apps.events.audit_actions import EventsAudit
from apps.events.collector import EventCollectionError
from apps.events.models import EventItem, EventSnapshot
from apps.events.sync import synchronize_events
from apps.membership.audit_actions import MembershipAudit
from apps.membership.collector import MembershipCollectionError
from apps.membership.models import MembershipCountObservation, MembershipFeedState
from apps.membership.sync import synchronize_membership
from apps.news.audit_actions import NewsAudit
from apps.news.collector import NewsCollectionError
from apps.news.models import NewsFeedState, NewsItem, NewsSnapshot
from apps.news.sync import synchronize_news
from apps.sources.models import ImportRun, ImportStatus, SourceArtifact

from .conftest import (
    collector_raising,
    collector_returning,
    event_collection,
    membership_collection,
    news_collection,
)

pytestmark = pytest.mark.django_db


# ======================================================================
# Membership
# ======================================================================


def test_membership_first_run_publishes_an_observation():
    outcome = synchronize_membership(collector=collector_returning(membership_collection(3000)))

    assert outcome.result == FeedResult.IMPORTED
    assert outcome.extra["total_members"] == 3000
    observation = MembershipCountObservation.objects.get()
    assert observation.is_current is True
    assert observation.total_members == 3000
    assert observation.import_run.status == ImportStatus.SUCCEEDED
    assert observation.import_run.dry_run is False


def test_membership_artifact_is_metadata_only():
    synchronize_membership(collector=collector_returning(membership_collection()))

    artifact = SourceArtifact.objects.get()
    assert artifact.is_external is True
    assert not artifact.file
    assert artifact.external_reference == "koda-public:company-list"
    assert artifact.sha256
    assert artifact.size_bytes > 0


def test_membership_repeat_run_reports_unchanged():
    collector = collector_returning(membership_collection(3000))
    synchronize_membership(collector=collector)

    outcome = synchronize_membership(collector=collector)

    assert outcome.result == FeedResult.UNCHANGED
    assert MembershipCountObservation.objects.count() == 1
    assert SourceArtifact.objects.count() == 1
    assert ImportRun.objects.filter(status=ImportStatus.SUCCEEDED, dry_run=False).count() == 1


def test_membership_changed_count_publishes_a_new_observation():
    synchronize_membership(collector=collector_returning(membership_collection(3000)))

    outcome = synchronize_membership(collector=collector_returning(membership_collection(3010)))

    assert outcome.result == FeedResult.IMPORTED
    assert MembershipCountObservation.objects.count() == 2
    assert MembershipCountObservation.objects.filter(is_current=True).count() == 1
    assert MembershipCountObservation.objects.get(is_current=True).total_members == 3010


def test_membership_suspicious_change_is_refused_and_keeps_the_previous():
    synchronize_membership(collector=collector_returning(membership_collection(3000)))
    good = MembershipCountObservation.objects.get()

    outcome = synchronize_membership(collector=collector_returning(membership_collection(100)))

    assert outcome.result == FeedResult.FAILED
    assert "ebausutavalt" in outcome.detail
    good.refresh_from_db()
    assert good.is_current is True
    assert MembershipCountObservation.objects.count() == 1


def test_membership_dry_run_publishes_nothing():
    outcome = synchronize_membership(
        collector=collector_returning(membership_collection()), dry_run=True
    )

    assert outcome.dry_run is True
    assert MembershipCountObservation.objects.count() == 0
    assert MembershipFeedState.objects.get().last_result == FeedResult.NEVER_RUN


def test_membership_dry_run_then_live_of_the_same_content_succeeds():
    collection = membership_collection(3000)
    synchronize_membership(collector=collector_returning(collection), dry_run=True)

    outcome = synchronize_membership(collector=collector_returning(collection))

    assert outcome.result == FeedResult.IMPORTED
    assert MembershipCountObservation.objects.count() == 1
    assert SourceArtifact.objects.count() == 1


def test_membership_failure_keeps_the_previous_observation():
    synchronize_membership(collector=collector_returning(membership_collection(3000)))
    good = MembershipCountObservation.objects.get()

    outcome = synchronize_membership(
        collector=collector_raising(MembershipCollectionError("Allikat ei leitud (404)."))
    )

    assert outcome.result == FeedResult.FAILED
    good.refresh_from_db()
    assert good.is_current is True
    state = MembershipFeedState.objects.get()
    assert state.last_result == FeedResult.FAILED
    assert "404" in state.last_error_summary


def test_membership_has_no_year_to_date_field():
    names = {field.name for field in MembershipCountObservation._meta.get_fields()}

    for forbidden in (
        "new_members_ytd",
        "new_members",
        "joined_this_year",
        "ytd",
        "new_members_this_year",
    ):
        assert forbidden not in names


#: The only two models allowed to hold one row per member. Everything else in
#: the app is an aggregate, and `test_membership_stores_no_member_rows` enforces
#: both halves of that sentence.
MEMBER_LEVEL_MODELS = {"MemberRegisterEntry", "MemberDirectoryEntry"}


def test_membership_stores_no_member_rows():
    """Only two named models may hold a member, and neither may hold a person.

    This assertion used to say that *no* model in the app could hold an
    individual member. That changed deliberately on 2026-08-15: the Chamber
    asked for the member list itself, which cannot be stored as an aggregate.

    What the rule protected is kept, and is worth stating precisely, because the
    two things are easy to confuse. The roster export carries a company name, a
    registry code, an address, a director and three contact addresses. Only the
    first two are facts about a *company*; the rest identify or reach a
    *person*, and those never enter the database. So:

    - every model outside `MEMBER_LEVEL_MODELS` stays aggregate-only, checked
      field by field exactly as before;
    - the two member-level models may name and code a company — that is what
      the page was asked for — but may not carry a director, an email, a phone,
      a fax, a postal address or the free-text comment column, and the check
      below is a substring match so `contact_email` cannot slip past a rule
      written for `email`.

    The allowlist is still the point: adding a model to this app should be a
    deliberate act that comes past this assertion.
    """
    from django.apps import apps as django_apps

    models = list(django_apps.get_app_config("membership").get_models())
    model_names = {model.__name__ for model in models}

    assert model_names == {
        # Aggregate composition of the member roster. The roster itself carries
        # a company name, a registry code, an address, a director and two
        # contact addresses; neither model below has a field capable of holding
        # any of them. A composition value is a vocabulary key, a vocabulary
        # label and an integer, and the snapshot beside it holds a date, a
        # checksum, a row count and coverage percentages.
        "MembershipCompositionSnapshot",
        "MembershipCompositionValue",
        # The public Koda.ee member directory.
        "MembershipCountObservation",
        "MembershipFeedState",
        # The Chamber's internal board-report history. Aggregates only.
        "InternalMembershipObservation",
        "MembershipHistoricalSourceDocument",
        "MembershipMonthlyNewMemberValue",
        "MembershipSizeMovement",
        "MembershipRemovalReason",
        "MembershipDataIssue",
        "MembershipMetricConflict",
        # Board-decision batches and new-member periods, added by the schema 2.0
        # import. Aggregates only: each of the five carries a `member_count`, a
        # size band or a reason key, an external batch/period identifier and its
        # provenance — and no field capable of naming, coding or linking to an
        # individual member. Checked field by field before being allowed here,
        # which is what this assertion is for.
        "MembershipDecisionBatch",
        "MembershipDecisionBatchReason",
        "MembershipDecisionBatchSizeMovement",
        "MembershipNewMemberPeriod",
        "MembershipNewMemberSizeDistribution",
        # The member list itself, imported by hand from the CRM. The snapshot is
        # an aggregate like every other — a date, a checksum, a row count — and
        # is checked as one below; only the entry beside it holds a member.
        "MemberRegisterSnapshot",
        "MemberRegisterEntry",
        # The public catalogue row by row, so the list can be compared against
        # what koda.ee publishes today. A registration code and a profile path:
        # both already public, and neither reaches a person.
        "MemberDirectoryEntry",
    }

    for model in models:
        if model.__name__ in MEMBER_LEVEL_MODELS:
            continue
        fields = {f.name for f in model._meta.get_fields()}
        for forbidden in (
            "crn",
            "reg_code",
            "registration_code",
            "name",
            "company",
            "member_url",
        ):
            assert forbidden not in fields, f"{model.__name__} gained a {forbidden} field"

    # The two member-level models carry company identity on purpose. What they
    # may never carry is a person, or a way to reach one.
    for model_name in MEMBER_LEVEL_MODELS:
        fields = {
            f.name for f in django_apps.get_model("membership", model_name)._meta.get_fields()
        }
        for forbidden in (
            "director",
            "manager",
            "contact",
            "person",
            "mail",
            "phone",
            "fax",
            "address",
            "comment",
            "note",
        ):
            offending = sorted(field for field in fields if forbidden in field)
            assert not offending, f"{model_name} gained {offending}, which can hold a person"


def test_membership_audit_carries_only_safe_facts():
    synchronize_membership(collector=collector_returning(membership_collection(3000)))

    event = AuditEvent.objects.get(action=MembershipAudit.OBSERVATION_IMPORTED)
    assert event.change_summary["total_members"] == 3000
    assert event.change_summary["source"] == "koda-public-members"
    assert "sha256" in event.change_summary
    blob = str(event.change_summary)
    assert "crn" not in blob
    assert "koda.ee/et/liikmed" not in blob


# ======================================================================
# News
# ======================================================================


def test_news_first_run_publishes_a_snapshot():
    outcome = synchronize_news(collector=collector_returning(news_collection(3)))

    assert outcome.result == FeedResult.IMPORTED
    snapshot = NewsSnapshot.objects.get()
    assert snapshot.is_current is True
    assert snapshot.item_count == 3
    assert NewsItem.objects.count() == 3


def test_news_items_keep_their_order_and_fields():
    synchronize_news(collector=collector_returning(news_collection(3)))

    items = list(NewsItem.objects.order_by("-published_at"))
    assert items[0].published_at > items[-1].published_at
    assert items[0].canonical_url.startswith("https://www.koda.ee/")
    assert items[0].title


def test_news_repeat_run_reports_unchanged():
    collector = collector_returning(news_collection(3))
    synchronize_news(collector=collector)

    outcome = synchronize_news(collector=collector)

    assert outcome.result == FeedResult.UNCHANGED
    assert NewsSnapshot.objects.count() == 1
    assert SourceArtifact.objects.count() == 1


def test_news_changed_feed_publishes_a_new_snapshot():
    synchronize_news(collector=collector_returning(news_collection(3)))

    outcome = synchronize_news(collector=collector_returning(news_collection(4)))

    assert outcome.result == FeedResult.IMPORTED
    assert NewsSnapshot.objects.count() == 2
    assert NewsSnapshot.objects.filter(is_current=True).count() == 1
    assert NewsSnapshot.objects.get(is_current=True).item_count == 4


def test_news_failure_keeps_the_previous_snapshot():
    synchronize_news(collector=collector_returning(news_collection(3)))
    good = NewsSnapshot.objects.get()

    outcome = synchronize_news(
        collector=collector_raising(NewsCollectionError("Uudisvoog ei ole kehtiv XML."))
    )

    assert outcome.result == FeedResult.FAILED
    good.refresh_from_db()
    assert good.is_current is True
    assert NewsFeedState.objects.get().last_result == FeedResult.FAILED


def test_news_dry_run_publishes_nothing():
    outcome = synchronize_news(collector=collector_returning(news_collection()), dry_run=True)

    assert outcome.dry_run is True
    assert NewsSnapshot.objects.count() == 0


def test_news_dry_run_then_live_of_the_same_content_succeeds():
    collection = news_collection(3)
    synchronize_news(collector=collector_returning(collection), dry_run=True)

    outcome = synchronize_news(collector=collector_returning(collection))

    assert outcome.result == FeedResult.IMPORTED
    assert NewsSnapshot.objects.count() == 1
    assert SourceArtifact.objects.count() == 1


def test_news_audit_carries_no_feed_body():
    synchronize_news(collector=collector_returning(news_collection(3)))

    event = AuditEvent.objects.get(action=NewsAudit.SNAPSHOT_IMPORTED)
    assert event.change_summary["item_count"] == 3
    blob = str(event.change_summary)
    assert "<rss" not in blob
    assert "Sünteetiline kokkuvõte" not in blob


# ======================================================================
# Events
# ======================================================================


def test_events_first_run_publishes_a_snapshot():
    outcome = synchronize_events(collector=collector_returning(event_collection(3)))

    assert outcome.result == FeedResult.IMPORTED
    snapshot = EventSnapshot.objects.get()
    assert snapshot.is_current is True
    assert snapshot.item_count == 3
    assert EventItem.objects.count() == 3


def test_events_date_only_items_have_no_invented_time():
    synchronize_events(collector=collector_returning(event_collection(2)))

    for item in EventItem.objects.all():
        assert item.starts_on is not None
        assert item.starts_at is None


def test_events_exact_timestamps_are_stored_when_supplied():
    synchronize_events(collector=collector_returning(event_collection(2, exact=True)))

    for item in EventItem.objects.all():
        assert item.starts_at is not None
        assert item.starts_at.astimezone(dt.timezone(dt.timedelta(hours=3))).hour == 14


def test_events_repeat_run_reports_unchanged():
    collector = collector_returning(event_collection(3))
    synchronize_events(collector=collector)

    outcome = synchronize_events(collector=collector)

    assert outcome.result == FeedResult.UNCHANGED
    assert EventSnapshot.objects.count() == 1
    assert SourceArtifact.objects.count() == 1


def test_events_changed_calendar_publishes_a_new_snapshot():
    synchronize_events(collector=collector_returning(event_collection(3)))

    outcome = synchronize_events(collector=collector_returning(event_collection(4)))

    assert outcome.result == FeedResult.IMPORTED
    assert EventSnapshot.objects.count() == 2
    assert EventSnapshot.objects.filter(is_current=True).count() == 1


def test_events_invalid_response_keeps_the_previous_snapshot():
    synchronize_events(collector=collector_returning(event_collection(3)))
    good = EventSnapshot.objects.get()

    outcome = synchronize_events(
        collector=collector_raising(EventCollectionError("Ühtegi tulevast sündmust ei leitud."))
    )

    assert outcome.result == FeedResult.FAILED
    good.refresh_from_db()
    assert good.is_current is True
    assert EventSnapshot.objects.filter(is_current=True).count() == 1


def test_events_dry_run_publishes_nothing():
    outcome = synchronize_events(collector=collector_returning(event_collection()), dry_run=True)

    assert outcome.dry_run is True
    assert EventSnapshot.objects.count() == 0


def test_events_dry_run_then_live_of_the_same_content_succeeds():
    collection = event_collection(3)
    synchronize_events(collector=collector_returning(collection), dry_run=True)

    outcome = synchronize_events(collector=collector_returning(collection))

    assert outcome.result == FeedResult.IMPORTED
    assert EventSnapshot.objects.count() == 1


def test_events_audit_carries_no_page_html():
    synchronize_events(collector=collector_returning(event_collection(3)))

    event = AuditEvent.objects.get(action=EventsAudit.SNAPSHOT_IMPORTED)
    assert event.change_summary["item_count"] == 3
    assert "<div" not in str(event.change_summary)


# ======================================================================
# Failure isolation between sources
# ======================================================================


def test_a_membership_failure_does_not_block_news_or_events():
    membership = synchronize_membership(
        collector=collector_raising(MembershipCollectionError("Allikat ei leitud (404)."))
    )
    news = synchronize_news(collector=collector_returning(news_collection(3)))
    events = synchronize_events(collector=collector_returning(event_collection(3)))

    assert membership.result == FeedResult.FAILED
    assert news.result == FeedResult.IMPORTED
    assert events.result == FeedResult.IMPORTED
    assert NewsSnapshot.objects.filter(is_current=True).count() == 1
    assert EventSnapshot.objects.filter(is_current=True).count() == 1


def test_a_news_failure_does_not_block_membership_or_events():
    news = synchronize_news(collector=collector_raising(NewsCollectionError("Vigane XML.")))
    membership = synchronize_membership(collector=collector_returning(membership_collection()))
    events = synchronize_events(collector=collector_returning(event_collection(2)))

    assert news.result == FeedResult.FAILED
    assert membership.result == FeedResult.IMPORTED
    assert events.result == FeedResult.IMPORTED


def test_an_events_failure_does_not_block_membership_or_news():
    events = synchronize_events(collector=collector_raising(EventCollectionError("Tühi leht.")))
    membership = synchronize_membership(collector=collector_returning(membership_collection()))
    news = synchronize_news(collector=collector_returning(news_collection(2)))

    assert events.result == FeedResult.FAILED
    assert membership.result == FeedResult.IMPORTED
    assert news.result == FeedResult.IMPORTED


def test_each_source_registers_its_own_safe_reference():
    synchronize_membership(collector=collector_returning(membership_collection()))
    synchronize_news(collector=collector_returning(news_collection(2)))
    synchronize_events(collector=collector_returning(event_collection(2)))

    references = set(SourceArtifact.objects.values_list("external_reference", flat=True))
    assert references == {
        "koda-public:company-list",
        "koda-public:news-feed",
        "koda-public:events",
    }
    for artifact in SourceArtifact.objects.all():
        assert not artifact.file
        assert "?" not in artifact.external_reference
        assert "http" not in artifact.external_reference
