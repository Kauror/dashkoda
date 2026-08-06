"""Priority hydration: eligibility decides what gets read, not age.

The defect these cases exist for: consultation eligibility is status-based —
open, and no opinion sent — and an earlier version hydrated only the recent
window, so a record whose consultation closed two years ago could never obtain a
link no matter how obviously it matched. The right page stayed permanently
unread.

Every archive entry here is synthetic and dated deliberately, some well outside
the one-year window, so "old" is a property of the fixture rather than of the
clock. No test contacts the public internet.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.legal_work.archived_topic_sync import synchronize_archived_topics
from apps.legal_work.current_topic_match_sync import run_current_topic_matching
from apps.legal_work.models import (
    ArchivedTopicItem,
    ArchivedTopicSnapshot,
    DetailStatus,
    LegalCurrentTopicMatchSnapshot,
    LegalWorkItem,
    MatchDecision,
    SentStatus,
)
from apps.legal_work.shortlist import (
    MAX_SHORTLIST_PER_RECORD,
    MIN_SHARED_SIGNIFICANT_TOKENS,
    shortlist_archive_urls,
)

from .archive_factory import ARCHIVE_PATH, DETAIL_PREFIX, archive_card, archive_listing
from .current_topic_factory import LISTING_PATH, FakeSite, card, detail, listing

pytestmark = pytest.mark.django_db

TODAY = dt.date.today()
RECENT = TODAY - dt.timedelta(days=20)
ANCIENT = TODAY - dt.timedelta(days=900)  # far outside the 365-day window

# The instrument the synthetic workbook's open record is about, so a shortlist
# built from listing text alone can find it.
OLD_SLUG = "mida-arvad-avatud-teema-muudatustest"
OLD_TITLE = "Mida arvad sünteetilise avatud teema muudatustest?"
OLD_SUMMARY = "Kliimaministeerium on koostanud sünteetilise avatud teema eelnõu."


def dated_detail(title: str, published: dt.date, *, body: str) -> str:
    return detail(
        title=title,
        date=published.strftime("%d.%m.%Y"),
        intro=f"{body} Anna hiljemalt 18. augustiks teada.",
        body=body,
    )


def archive_with_old_candidate(*, recent_pages: int = 2) -> FakeSite:
    """An archive whose *oldest* page holds the entry a live record needs.

    The old candidate sits on the last page, behind enough recent entries that a
    window-limited crawl would never reach it — which is exactly the shape of
    the defect.
    """
    pages: dict[str, str] = {}
    last = recent_pages
    for page in range(recent_pages):
        # Deliberately share no discriminating word with the workbook record:
        # the point of the fixture is that only the *old* page is a candidate,
        # so a budget of one has exactly one right answer.
        cards = [
            archive_card(
                f"recent-{page}-{i}",
                f"Kalanduskvootide jaotamise kord {page}-{i}",
                summary="Regionaalministeerium muutis kalanduskvootide jaotamist.",
            )
            for i in range(2)
        ]
        key = ARCHIVE_PATH if page == 0 else f"{ARCHIVE_PATH}?page={page}"
        pages[key] = archive_listing(*cards, current=page, last=last)
        for i in range(2):
            pages[f"{DETAIL_PREFIX}recent-{page}-{i}"] = dated_detail(
                f"Kalanduskvootide jaotamise kord {page}-{i}",
                RECENT,
                body="Kalanduskvootide jaotamise korra muutmine.",
            )
    pages[f"{ARCHIVE_PATH}?page={last}"] = archive_listing(
        archive_card(OLD_SLUG, OLD_TITLE, summary=OLD_SUMMARY), current=last, last=last
    )
    pages[f"{DETAIL_PREFIX}{OLD_SLUG}"] = dated_detail(
        OLD_TITLE, ANCIENT, body="Sünteetilise avatud teema eelnõu vana sisu."
    )
    return FakeSite(pages)


@pytest.fixture
def current_published(imported_snapshot, publish_current_topics):
    """A current catalogue that matches nothing, so every record needs the archive."""
    publish_current_topics(
        FakeSite(
            {
                LISTING_PATH: listing(card("miski", "Täiesti seosetu teema")),
                f"{DETAIL_PREFIX}miski": detail(title="Täiesti seosetu teema"),
            }
        )
    )
    run_current_topic_matching()
    return imported_snapshot


@pytest.fixture
def archive_sync(monkeypatch, settings):
    settings.KODA_ARCHIVE_REQUEST_PAUSE_SECONDS = 0

    def run(site, **kwargs):
        monkeypatch.setattr("apps.legal_work.archived_topics.fetch", site)
        kwargs.setdefault("full", True)
        return synchronize_archived_topics(**kwargs)

    return run


def old_row():
    return ArchivedTopicItem.objects.get(
        snapshot__is_current=True, canonical_url__endswith=OLD_SLUG
    )


# -- the defect, and its correction -----------------------------------------


def test_an_old_candidate_for_an_eligible_record_is_hydrated(current_published, archive_sync):
    """The case the whole correction exists for.

    The record is open and unsent; its consultation closed 900 days ago and sits
    on the archive's last page. Age must not decide whether it can be read.
    """
    report = archive_sync(archive_with_old_candidate(), max_detail_pages=3)

    row = old_row()
    assert row.detail_status == DetailStatus.HYDRATED
    assert row.published_date == ANCIENT
    assert report.priority_candidate_count >= 1
    assert report.priority_detailed_count >= 1


def test_the_old_candidate_is_read_before_unrelated_recent_pages(current_published, archive_sync):
    """A budget of one must spend it on the page a link depends on."""
    archive_sync(archive_with_old_candidate(), max_detail_pages=1)

    assert old_row().detail_status == DetailStatus.HYDRATED
    recent = ArchivedTopicItem.objects.filter(
        snapshot__is_current=True, canonical_url__contains="recent-"
    )
    assert not recent.filter(detail_status=DetailStatus.HYDRATED).exists()


def test_the_old_candidate_is_present_in_the_complete_index(current_published, archive_sync):
    report = archive_sync(archive_with_old_candidate(), max_detail_pages=0)

    assert report.index_complete is True
    assert ArchivedTopicItem.objects.filter(
        snapshot__is_current=True, canonical_url__endswith=OLD_SLUG
    ).exists()


def test_an_old_hydrated_candidate_can_participate_in_matching(current_published, archive_sync):
    from apps.legal_work.archived_topic_match_sync import run_archive_matching

    archive_sync(archive_with_old_candidate(), max_detail_pages=5)

    report = run_archive_matching()

    assert report.result != "failed"
    assert report.considered_items >= 1


# -- who does and does not cause priority hydration --------------------------


def test_a_sent_record_causes_no_priority_hydration(current_published, archive_sync):
    LegalWorkItem.objects.filter(snapshot=current_published).update(
        sent_status=SentStatus.SENT, sent_date=TODAY, is_open=True
    )

    report = archive_sync(archive_with_old_candidate(), max_detail_pages=2)

    assert report.priority_candidate_count == 0


def test_a_closed_record_causes_no_priority_hydration(current_published, archive_sync):
    LegalWorkItem.objects.filter(snapshot=current_published).update(is_open=False)

    report = archive_sync(archive_with_old_candidate(), max_detail_pages=2)

    assert report.priority_candidate_count == 0


def test_a_record_already_matched_by_the_current_listing_causes_no_priority_hydration(
    current_published, archive_sync
):
    from apps.legal_work.models import CurrentTopicItem, LegalCurrentTopicMatch

    snapshot = LegalCurrentTopicMatchSnapshot.objects.get(is_current=True)
    candidate = CurrentTopicItem.objects.get(snapshot__is_current=True)
    LegalCurrentTopicMatch.objects.filter(snapshot=snapshot).update(
        decision=MatchDecision.MATCHED, best_candidate=candidate
    )

    report = archive_sync(archive_with_old_candidate(), max_detail_pages=2)

    assert report.priority_candidate_count == 0


def test_priority_hydration_is_skipped_safely_without_a_current_match_snapshot(
    imported_snapshot, archive_sync
):
    """No current matcher run yet: collect and background-hydrate, prioritise nobody."""
    report = archive_sync(archive_with_old_candidate(), max_detail_pages=2)

    assert report.result != "failed"
    assert report.priority_candidate_count == 0
    assert report.indexed_items > 0


# -- budget, dedup and resumption --------------------------------------------


def test_one_page_shortlisted_by_several_records_costs_one_request(current_published):
    """Deduplicated across records: the shortlist is a set of URLs."""
    from apps.legal_work.archived_topics import ArchiveListingEntry

    entries = [
        ArchiveListingEntry(
            content_key="k1",
            canonical_url=f"https://www.koda.ee{DETAIL_PREFIX}{OLD_SLUG}",
            title=OLD_TITLE,
            listing_summary=OLD_SUMMARY,
            source_page=0,
            source_order=0,
        )
    ]
    records = list(LegalWorkItem.objects.filter(snapshot=current_published)[:3])

    urls = shortlist_archive_urls(entries, records)

    assert len(urls) <= 1  # one page, one request, however many records wanted it


def test_priority_candidates_beyond_the_budget_resume_on_a_later_run(
    current_published, archive_sync
):
    first = archive_sync(archive_with_old_candidate(), max_detail_pages=0)
    assert first.priority_pending_count >= 1
    assert first.backfill_complete is False

    second = archive_sync(archive_with_old_candidate(), max_detail_pages=10)

    assert second.priority_pending_count == 0
    assert old_row().detail_status == DetailStatus.HYDRATED


def test_remaining_budget_goes_to_the_recent_background_window(current_published, archive_sync):
    archive_sync(archive_with_old_candidate(), max_detail_pages=10)

    recent = ArchivedTopicItem.objects.filter(
        snapshot__is_current=True, canonical_url__contains="recent-"
    )
    assert recent.filter(detail_status=DetailStatus.HYDRATED).exists()


def test_no_budget_means_nothing_is_hydrated(current_published, archive_sync):
    report = archive_sync(archive_with_old_candidate(), max_detail_pages=0)

    assert report.detail_requests == 0
    assert report.detailed_items == 0
    assert report.indexed_items > 0


# -- completion semantics ----------------------------------------------------


def test_backfill_is_incomplete_while_a_priority_candidate_is_unread(
    current_published, archive_sync
):
    report = archive_sync(archive_with_old_candidate(), max_detail_pages=0)

    assert report.index_complete is True
    assert report.backfill_complete is False


def test_backfill_completes_once_priority_and_recent_work_is_done(current_published, archive_sync):
    report = archive_sync(archive_with_old_candidate(), max_detail_pages=50)

    assert report.priority_pending_count == 0
    assert report.backfill_complete is True


def test_full_hydration_of_the_whole_archive_is_not_required(current_published, archive_sync):
    """Completion is about work a link depends on, not about every page."""
    settings_free_site = archive_with_old_candidate(recent_pages=2)
    report = archive_sync(settings_free_site, max_detail_pages=50)

    assert report.backfill_complete is True
    # Not every indexed entry needs to have been read for that to be true.
    assert report.detailed_items <= report.indexed_items


def test_an_unchanged_listing_with_pending_priority_work_is_not_reported_unchanged(
    current_published, archive_sync
):
    site = archive_with_old_candidate()
    first = archive_sync(site, max_detail_pages=0)
    second = archive_sync(site, max_detail_pages=0)

    assert first.backfill_complete is False
    assert second.result != "unchanged"


def test_an_unchanged_listing_with_no_work_left_is_reported_unchanged(
    current_published, archive_sync
):
    site = archive_with_old_candidate()
    archive_sync(site, max_detail_pages=50)
    repeat = archive_sync(site, max_detail_pages=50)

    assert repeat.result == "unchanged"


def test_a_new_legal_snapshot_can_create_hydration_work_without_a_listing_change(
    current_published, archive_sync, make_workbook, register_workbook
):
    """The archive did not change; who needs it did."""
    from apps.legal_work.importer import import_artifact

    from .workbook_factory import synthetic_row

    site = archive_with_old_candidate()
    archive_sync(site, max_detail_pages=50)

    # A newer workbook introduces a record whose candidate is the ancient page.
    rows = [
        synthetic_row(
            record_id="SYN-OLD",
            topic="Sünteetilise avatud teema muutmise seaduse eelnõu",
            received_date=TODAY - dt.timedelta(days=5),
            is_open=True,
            source_row=2,
        )
    ]
    import_artifact(register_workbook(make_workbook(rows=rows)), dry_run=False)
    run_current_topic_matching()

    after = archive_sync(site, max_detail_pages=50)

    assert after.priority_candidate_count >= 1


# -- failures ----------------------------------------------------------------


def test_a_failed_priority_detail_is_recorded_and_does_not_stop_the_run(
    current_published, archive_sync
):
    from apps.core.public_http import PublicFetchError

    site = archive_with_old_candidate()
    site.errors[f"{DETAIL_PREFIX}{OLD_SLUG}"] = PublicFetchError("Allikat ei leitud (404).")

    report = archive_sync(site, max_detail_pages=10)

    assert report.result != "failed"
    row = old_row()
    assert row.detail_status == DetailStatus.FAILED
    assert row.detail_failure_code == "http_404"
    assert report.indexed_items > 0


def test_a_terminally_failed_priority_candidate_does_not_hold_completion_open(
    current_published, archive_sync
):
    from apps.core.public_http import PublicFetchError

    site = archive_with_old_candidate()
    site.errors[f"{DETAIL_PREFIX}{OLD_SLUG}"] = PublicFetchError("Allikat ei leitud (404).")

    report = archive_sync(site, max_detail_pages=50)

    assert report.priority_pending_count == 0
    assert report.priority_failed_count >= 1


def test_a_failed_candidate_is_retried_on_a_later_run(current_published, archive_sync):
    from apps.core.public_http import PublicFetchError

    broken = archive_with_old_candidate()
    broken.errors[f"{DETAIL_PREFIX}{OLD_SLUG}"] = PublicFetchError("Allikat ei leitud (404).")
    archive_sync(broken, max_detail_pages=10)
    assert old_row().detail_status == DetailStatus.FAILED

    archive_sync(archive_with_old_candidate(), max_detail_pages=10)

    assert old_row().detail_status == DetailStatus.HYDRATED


# -- shortlist contract ------------------------------------------------------


def test_the_shortlist_constants_are_named_and_bounded():
    assert MAX_SHORTLIST_PER_RECORD > 0
    assert MIN_SHARED_SIGNIFICANT_TOKENS >= 1


def test_the_shortlist_searches_every_year_not_just_the_window():
    """Age cannot enter the shortlist: listing cards carry no year."""
    import inspect

    source = inspect.getsource(shortlist_archive_urls)

    for forbidden in ("published_date", "cutoff", "hydration_cutoff", "window"):
        assert forbidden not in source.split('"""')[2], f"{forbidden} must not gate the shortlist"


def test_the_archive_snapshot_is_still_unique(current_published, archive_sync):
    archive_sync(archive_with_old_candidate(), max_detail_pages=5)

    assert ArchivedTopicSnapshot.objects.filter(is_current=True).count() == 1
