"""The browser-suite seed: refusal, determinism, idempotency and shape.

The seed exists so the browser suite meets realistic content instead of empty
states. These tests run it against a real database and assert the properties the
browser suite depends on — and the one property nothing else can check, that it
refuses to touch a production database.
"""

from __future__ import annotations

import os
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import models

from apps.core import e2e_seed as core_seed
from apps.core.management.commands import seed_e2e_data
from apps.event_programme import e2e_seed as event_programme_seed
from apps.event_programme.models import (
    DeliveryMode,
    EventProgrammeItem,
    EventProgrammeSnapshot,
    EventStatus,
)
from apps.events.models import EventItem, EventSnapshot
from apps.legal_work import e2e_seed as legal_work_seed
from apps.legal_work.models import LegalWorkItem, LegalWorkSnapshot
from apps.membership.models import InternalMembershipObservation, MembershipCountObservation
from apps.news.models import NewsItem, NewsSnapshot
from apps.sources.models import ImportRun, ImportStatus
from apps.visibility import e2e_seed as visibility_seed
from apps.visibility.models import VisibilityObservation

pytestmark = pytest.mark.django_db


def run_seed() -> str:
    output = StringIO()
    call_command("seed_e2e_data", stdout=output)
    return output.getvalue()


# -- it refuses production ----------------------------------------------


def test_the_seed_refuses_to_run_under_production_settings(monkeypatch):
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.production")

    with pytest.raises(CommandError) as error:
        call_command("seed_e2e_data", stdout=StringIO())

    assert "production" in str(error.value)


def test_the_seed_refuses_an_unset_settings_module(monkeypatch):
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)

    with pytest.raises(CommandError):
        call_command("seed_e2e_data", stdout=StringIO())


def test_production_is_not_in_the_permitted_set():
    assert "config.settings.production" not in seed_e2e_data.ALLOWED_SETTINGS_MODULES
    assert seed_e2e_data.ALLOWED_SETTINGS_MODULES == {
        "config.settings.local",
        "config.settings.test",
    }


def test_the_test_run_itself_is_under_a_permitted_module():
    assert os.environ.get("DJANGO_SETTINGS_MODULE") in seed_e2e_data.ALLOWED_SETTINGS_MODULES


# -- it publishes through the real domain paths -------------------------


def test_the_seed_publishes_every_wired_module():
    run_seed()

    # One current snapshot per feed, published atomically.
    assert LegalWorkSnapshot.objects.filter(is_current=True).count() == 1
    assert EventProgrammeSnapshot.objects.filter(is_current=True).count() == 1
    assert EventSnapshot.objects.filter(is_current=True).count() == 1
    assert NewsSnapshot.objects.filter(is_current=True).count() == 1
    assert MembershipCountObservation.objects.filter(is_current=True).count() == 1

    assert LegalWorkItem.objects.count() >= 20
    # More than one page of 50, so the programme table's pagination is exercised.
    assert EventProgrammeItem.objects.count() > 50
    assert EventItem.objects.count() >= 15
    assert NewsItem.objects.count() >= 10
    # Six board reports, so both overview trend lines have enough points.
    assert InternalMembershipObservation.objects.count() == 6
    assert VisibilityObservation.objects.exists()


def test_every_import_run_the_seed_created_reached_a_successful_terminal_state():
    run_seed()

    runs = ImportRun.objects.all()
    assert runs.exists()
    assert not runs.exclude(status=ImportStatus.SUCCEEDED).exists()


def test_the_legal_work_workbook_passed_the_real_parser():
    """The seed writes a genuine XLSX and imports it through the real importer,
    so a workbook the parser would reject cannot be seeded."""
    run_seed()

    snapshot = LegalWorkSnapshot.objects.get(is_current=True)
    # CONTROL must agree with DATA or the parser refuses the file outright.
    assert snapshot.total_record_count == LegalWorkItem.objects.filter(snapshot=snapshot).count()
    assert snapshot.open_record_count > 0
    assert snapshot.sent_record_count > 0
    assert snapshot.warning_record_count > 0


# -- the content the browser suite depends on ---------------------------


def test_the_seed_creates_content_long_enough_to_truncate():
    """The 152-pixel overflow only appeared with content longer than the card.

    A short fixture cannot reproduce it, so the seed's long values are part of
    the contract rather than decoration.
    """
    run_seed()

    longest_topic = max(LegalWorkItem.objects.values_list("topic", flat=True), key=len)
    longest_event = max(EventItem.objects.values_list("title", flat=True), key=len)
    longest_news = max(NewsItem.objects.values_list("title", flat=True), key=len)

    assert len(longest_topic) > 150
    assert len(longest_event) > 150
    assert len(longest_news) > 150


def test_an_explicit_zero_and_a_missing_value_both_exist():
    """The interface must distinguish "counted, and it was none" from "nobody
    counted". Seeding only one of the two would let a regression hide."""
    run_seed()

    suspended = list(
        InternalMembershipObservation.objects.values_list("suspended_members", flat=True)
    )

    assert 0 in suspended, "an explicitly reported zero must be seeded"
    assert None in suspended, "a genuinely missing value must be seeded"


def test_the_event_programme_workbook_passed_the_real_parser():
    """The seed writes a genuine XLSX and imports it through the real importer.

    Writing `EventProgrammeItem` rows directly would let the seed publish a
    programme the canonical contract would reject, which is the one thing the
    browser stage must not be able to do.
    """
    run_seed()

    snapshot = EventProgrammeSnapshot.objects.get(is_current=True)
    items = EventProgrammeItem.objects.filter(snapshot=snapshot)
    # DASH_CONTROL must agree with DASH_EVENTS or the parser refuses the file.
    assert snapshot.canonical_event_count == items.count()
    assert snapshot.dated_event_count == items.exclude(start_date=None).count()
    assert snapshot.linked_public_url_count == items.exclude(public_url="").count()
    assert snapshot.artifact.is_external, "the seeded artifact must carry no stored file"


def test_the_seeded_programme_covers_every_shape_the_page_has_to_render():
    run_seed()

    items = EventProgrammeItem.objects.all()

    assert len({item.event_year for item in items if item.event_year}) >= 3, "several years"
    assert len({item.event_month_key[-2:] for item in items if item.event_month_key}) >= 4
    assert {item.event_quarter for item in items} >= {"Q1", "Q2", "Q4"}
    assert {item.event_status for item in items} == {
        EventStatus.PAST,
        EventStatus.ONGOING,
        EventStatus.UPCOMING,
        EventStatus.DATE_UNKNOWN,
    }
    # All three stated modes **and** the blank one. A delivery mode the source
    # never stated is `Määramata`, never `Kohapeal`, and the dashboard has to be
    # able to show that on a real seeded row rather than only in a unit test.
    assert {item.delivery_mode for item in items} == {
        DeliveryMode.ONSITE,
        DeliveryMode.ONLINE,
        DeliveryMode.HYBRID,
        "",
    }
    assert {item.price_status for item in items} >= {"paid", "free", "missing", "tba"}
    assert items.filter(planning_lead_days__gt=0).exists(), "a planned event"
    assert items.filter(planning_lead_days__lt=0).exists(), "a retroactively entered event"
    assert items.filter(added_date=None).exists(), "an event with no planning data"
    assert len({item.tag_key for item in items}) >= 3
    assert len({item.event_type_key for item in items}) >= 2
    assert items.filter(start_date=None).exists(), "an undated record"
    assert items.filter(end_date__gt=models.F("start_date")).exists(), "a date range"
    assert items.exclude(public_url="").exists(), "a linked event"
    assert items.filter(public_url="").exists(), "an unlinked event"
    assert items.filter(review_required=True).exists(), "a review-required record"


def test_the_seeded_programme_has_a_linked_name_long_enough_to_truncate():
    run_seed()

    linked = EventProgrammeItem.objects.exclude(public_url="")
    assert max(len(item.event_name) for item in linked) > 150


def test_a_quarter_boundary_is_seeded_on_consecutive_days():
    """31 March and 1 April: the two days quarter filtering most easily confuses."""
    run_seed()

    end_of_q1 = EventProgrammeItem.objects.filter(start_date__month=3, start_date__day=31).first()
    start_of_q2 = EventProgrammeItem.objects.filter(start_date__month=4, start_date__day=1).first()

    assert end_of_q1 is not None and end_of_q1.event_quarter == "Q1"
    assert start_of_q2 is not None and start_of_q2.event_quarter == "Q2"
    assert (start_of_q2.start_date - end_of_q1.start_date).days == 1


def test_the_seeded_programme_urls_are_obviously_not_production():
    run_seed()

    for url in EventProgrammeItem.objects.exclude(public_url="").values_list(
        "public_url", flat=True
    ):
        assert url.startswith("https://www.koda.ee/et/sundmused/"), "an allowed host"
        assert "sunteetiline" in url, "and an unmistakably synthetic path"


def test_the_seeded_event_programme_workbook_is_byte_identical_across_a_second_boundary(tmp_path):
    """Idempotency depends on it, for the same reason as the legal-work export."""
    import datetime as dt
    import hashlib
    import time

    today = dt.date(2099, 6, 1)
    first = event_programme_seed.write_workbook(tmp_path / "first.xlsx", today)
    time.sleep(1.1)
    second = event_programme_seed.write_workbook(tmp_path / "second.xlsx", today)

    assert (
        hashlib.sha256(first.read_bytes()).hexdigest()
        == hashlib.sha256(second.read_bytes()).hexdigest()
    )


def test_the_seeded_event_programme_workbook_satisfies_the_real_contract(tmp_path):
    import datetime as dt

    from apps.event_programme.workbook import parse_workbook

    path = event_programme_seed.write_workbook(tmp_path / "programme.xlsx", dt.date(2099, 6, 1))
    parsed = parse_workbook(path)

    assert len(parsed.rows) > 50
    assert parsed.dated_event_count == len(parsed.rows) - 1
    # Several linked events now, because the cross-domain joins need them: GA4
    # files traffic under a path, the shop files its event product under a path,
    # and the programme's own `public_url` is what ties an event to both.
    assert parsed.linked_public_url_count > 1
    assert parsed.review_required_count == 1


def test_events_span_month_and_year_boundaries():
    run_seed()

    months = {item.starts_on.month for item in EventItem.objects.all()}
    years = {item.starts_on.year for item in EventItem.objects.all()}

    assert len(months) > 1, "date formatting breaks most often at a month boundary"
    assert len(years) > 1, "and at a year boundary"


def test_both_dated_and_ranged_events_exist():
    run_seed()

    assert EventItem.objects.filter(ends_on__isnull=True).exists()
    assert EventItem.objects.filter(ends_on__isnull=False).exists()


def test_legal_work_covers_dated_and_undated_deadlines():
    run_seed()

    open_items = LegalWorkItem.objects.filter(is_open=True)
    assert open_items.filter(deadline_date__isnull=False).exists()
    assert open_items.filter(deadline_date__isnull=True).exists()


# -- determinism and idempotency ----------------------------------------


def test_running_the_seed_twice_publishes_nothing_new():
    """A cron-safe seed must be re-runnable: CI may seed a database that a
    previous step already seeded."""

    def counts() -> tuple[int, ...]:
        return (
            LegalWorkSnapshot.objects.count(),
            EventProgrammeSnapshot.objects.count(),
            EventSnapshot.objects.count(),
            NewsSnapshot.objects.count(),
            InternalMembershipObservation.objects.count(),
            VisibilityObservation.objects.count(),
        )

    run_seed()
    before = counts()

    run_seed()

    assert counts() == before


def test_the_seeded_workbook_is_byte_identical_across_a_second_boundary(tmp_path):
    """Idempotency depends on this, and it is not free.

    An XLSX carries the current time twice: in the ZIP member headers, and in
    `dcterms:modified` inside `docProps/core.xml`, which openpyxl re-stamps
    while saving. The synchronisation deduplicates on the checksum of those
    bytes, so unfrozen timestamps made the seed publish a fresh snapshot every
    run.

    The sleep is the point. Two builds inside the same second hash identically
    even with the timestamps unfrozen, so a test without it passes against the
    broken code — which is exactly what happened before CI caught it.
    """
    import datetime as dt
    import hashlib
    import time

    today = dt.date(2099, 6, 1)
    first = legal_work_seed.write_workbook(tmp_path / "first.xlsx", today)
    time.sleep(1.1)
    second = legal_work_seed.write_workbook(tmp_path / "second.xlsx", today)

    assert (
        hashlib.sha256(first.read_bytes()).hexdigest()
        == hashlib.sha256(second.read_bytes()).hexdigest()
    )


def test_the_seeded_workbook_still_satisfies_the_real_contract(tmp_path):
    """Freezing timestamps must not have produced a package the parser rejects."""
    import datetime as dt

    from apps.legal_work.workbook import parse_workbook

    path = legal_work_seed.write_workbook(tmp_path / "seed.xlsx", dt.date(2099, 6, 1))
    parsed = parse_workbook(path)

    assert len(parsed.rows) > 20
    assert parsed.open_count > 0
    assert parsed.sent_count > 0
    assert parsed.warning_record_count > 0


def test_the_seed_is_deterministic_in_its_values():
    """No randomness: the same day must produce the same numbers, or a failing
    browser test could not be reproduced."""
    run_seed()
    totals = sorted(InternalMembershipObservation.objects.values_list("total_members", flat=True))

    assert totals == [4050, 4090, 4120, 4150, 4176, 4203]


# -- nothing real, nothing fetched --------------------------------------


def test_the_seed_stores_no_real_looking_identifier():
    run_seed()

    for url in EventItem.objects.values_list("canonical_url", flat=True):
        assert "sunteetiline" in url
    for url in NewsItem.objects.values_list("canonical_url", flat=True):
        assert "sunteetiline" in url


def test_the_seed_opens_no_socket(monkeypatch):
    """Seeding is offline by construction: every collector is a local callable."""
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("seed_e2e_data must not open a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    run_seed()


def test_the_seed_keeps_no_workbook_behind(tmp_path, settings):
    """The legal-work artifact is metadata-only, exactly as in production."""
    settings.SOURCE_ARTIFACT_ROOT = str(tmp_path)

    run_seed()

    snapshot = LegalWorkSnapshot.objects.get(is_current=True)
    assert snapshot.artifact.is_external, "the seeded artifact must carry no stored file"
    assert not any(tmp_path.rglob("*.xlsx"))


# -- the website analytics ----------------------------------------------


def test_the_seed_connects_the_website_section():
    """Without this the whole traffic section is invisible to the browser suite.

    `overview.html` gates it on `page.ga4.is_connected`, which is false until a
    current reporting day exists — so before the seed published analytics the
    chart, the channel table, the content ranking and the page search rendered
    as the `Lisamisel` empty state in every browser run and in every screenshot
    CI uploaded. Two defects shipped through a green suite in that blind spot.
    """
    from apps.visibility.ga4 import get_connection_status
    from apps.visibility.models import Ga4ChannelDaily, Ga4DailySnapshot, Ga4PageDaily

    run_seed()

    assert get_connection_status().is_connected
    days = Ga4DailySnapshot.objects.filter(is_current_for_date=True)
    assert days.count() == visibility_seed.ANALYTICS_DAYS
    assert days.filter(has_page_detail=True).count() == visibility_seed.ANALYTICS_DAYS
    assert Ga4PageDaily.objects.exists()
    assert Ga4ChannelDaily.objects.exists()


def test_the_seeded_site_total_is_the_sum_of_its_page_rows():
    """What makes "excluded from a list, never from a total" checkable here."""
    from django.db.models import Sum

    from apps.visibility.models import Ga4DailySnapshot, Ga4PageDaily

    run_seed()

    for snapshot in Ga4DailySnapshot.objects.filter(is_current_for_date=True):
        rows = Ga4PageDaily.objects.filter(snapshot=snapshot).aggregate(total=Sum("page_views"))
        assert snapshot.page_views == rows["total"]


def test_the_seeded_ranking_excludes_utility_paths_but_keeps_their_traffic():
    """The registry is only demonstrable if the seed gives it something to do.

    `/et` outranks every seeded article on purpose, because that is how the real
    property behaves — 133 588 page views against a few thousand for the best
    article. A seed without it could not tell a working exclusion registry from
    an absent one.
    """
    from apps.visibility.ga4_selectors import get_traffic_series
    from apps.visibility.traffic_page import build_traffic_section

    run_seed()

    section = build_traffic_section(period_key="koik")
    ranked = {row.path for row in section.ranking}
    assert ranked
    for excluded in ("/et", "/en", "/ru", "/et/search/node", "/et/cart", "/403.html"):
        assert excluded not in ranked

    # And none of it was deleted: the site's own figures still carry every view.
    series = get_traffic_series(start=section.start, end=section.end)
    assert series.total_page_views > sum(row.page_views for row in section.ranking)


def test_the_seeded_ranking_shows_a_title_long_enough_to_truncate():
    """The overflow candidate has to be *on screen* to be measured.

    A very long linked title carrying a visually hidden suffix is the shape that
    widened a page by 152 pixels once, so the seed gives it the heaviest traffic
    in its section rather than leaving it at rank 34 where no layout assertion
    would ever reach it.
    """
    from apps.visibility.traffic_page import build_traffic_section

    run_seed()

    labels = [row.label for row in build_traffic_section(period_key="koik").ranking]
    assert core_seed.LONG_TITLE in labels


def test_the_seeded_history_is_searchable_beyond_the_ranking():
    """The page search, over seeded data, by path and by catalogued title."""
    from apps.visibility.traffic_page import build_traffic_section

    run_seed()

    quiet = visibility_seed.ANALYTICS_QUIET_PATH
    ranking = build_traffic_section(period_key="koik")
    assert quiet not in {row.path for row in ranking.ranking}

    # By path.
    found = build_traffic_section(period_key="koik", search=quiet)
    assert [row.path for row in found.results] == [quiet]

    # And by its catalogued title, which appears in no path at all — so this
    # passes only because `synchronize_news` really did catalogue the article.
    by_title = build_traffic_section(
        period_key="koik", search=visibility_seed.ANALYTICS_QUIET_TITLE_TERM
    )
    assert quiet in {row.path for row in by_title.results}

    # Enough matches that the results paginate, so the pager is reachable.
    everything = build_traffic_section(period_key="koik", search="sunteetiline")
    assert everything.total_pages > 1


def test_the_seed_covers_both_a_named_page_and_an_unnamed_one():
    """A ranking that showed only titles, or only paths, would hide half the
    rendering. News are catalogued by their sync; events and services are not,
    so their rows must fall back to the path rather than invent a name."""
    from apps.visibility.traffic_page import build_traffic_section

    run_seed()

    rows = build_traffic_section(period_key="koik").ranking
    assert any(row.has_known_identity for row in rows), "no row resolved to a title"
    assert any(not row.has_known_identity for row in rows), "every row had a title"


def test_a_second_seed_publishes_no_new_reporting_day():
    """The GA4 payload is hashed, so a non-deterministic figure would show up
    here as a database full of revisions of the same day."""
    from apps.visibility.models import Ga4DailySnapshot

    run_seed()
    before = Ga4DailySnapshot.objects.count()

    run_seed()

    assert Ga4DailySnapshot.objects.count() == before
