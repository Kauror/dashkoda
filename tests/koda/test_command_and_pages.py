"""The sync_koda_public command, and the pages the three feeds render.

No HTTP happens here: the command's synchronise functions are patched with
synthetic collectors.
"""

from __future__ import annotations

import json
import re
from io import StringIO

import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.core.feeds import FeedResult, advisory_lock
from apps.events.collector import EventCollectionError
from apps.events.sync import LOCK_NAME as EVENTS_LOCK
from apps.events.sync import synchronize_events
from apps.membership.collector import MembershipCollectionError
from apps.membership.sync import synchronize_membership
from apps.news.collector import NewsCollectionError
from apps.news.sync import synchronize_news

from .conftest import (
    collector_raising,
    collector_returning,
    event_collection,
    membership_collection,
    news_collection,
)

pytestmark = pytest.mark.django_db

MODULE = "apps.core.management.commands.sync_koda_public"


@pytest.fixture
def wire(monkeypatch):
    """Point the command at synthetic collectors."""

    def apply(*, membership=None, news=None, events=None):
        import apps.core.management.commands.sync_koda_public as command_module

        membership = membership if membership is not None else membership_collection()
        news = news if news is not None else news_collection(3)
        events = events if events is not None else event_collection(3)

        def make(sync, collection):
            def run(*, dry_run=False):
                collector = (
                    collector_raising(collection)
                    if isinstance(collection, Exception)
                    else collector_returning(collection)
                )
                return sync(dry_run=dry_run, collector=collector)

            return run

        monkeypatch.setitem(
            command_module.SOURCES,
            "membership",
            ("lock.m", make(synchronize_membership, membership)),
        )
        monkeypatch.setitem(
            command_module.SOURCES, "news", ("lock.n", make(synchronize_news, news))
        )
        monkeypatch.setitem(
            command_module.SOURCES, "events", ("lock.e", make(synchronize_events, events))
        )

    return apply


def run(*arguments) -> tuple[str, int]:
    output = StringIO()
    code = 0
    try:
        call_command("sync_koda_public", *arguments, stdout=output, stderr=output)
    except SystemExit as exit_info:
        code = exit_info.code
    return output.getvalue(), code


# -- the command --------------------------------------------------------


def test_all_sources_succeed(wire):
    wire()

    output, code = run("--json")

    assert code == 0
    payload = json.loads(output.strip())
    assert payload["result"] == "succeeded"
    assert set(payload["sources"]) == {"membership", "news", "events"}
    assert payload["sources"]["membership"]["result"] == FeedResult.IMPORTED
    assert payload["sources"]["membership"]["total_members"] == 3000
    assert payload["sources"]["news"]["items"] == 3
    assert payload["sources"]["events"]["items"] == 3


def test_json_output_is_exactly_one_line(wire):
    wire()

    output, _code = run("--json")

    assert len(output.strip().splitlines()) == 1


def test_json_output_carries_no_member_or_payload_content(wire):
    wire()

    output, _code = run("--json")

    for forbidden in ("crn", "registrikood", "<rss", "<div", "liikmed/", "@graph"):
        assert forbidden not in output


def test_one_failing_source_still_publishes_the_others(wire):
    wire(news=NewsCollectionError("Uudisvoog ei ole kehtiv XML."))

    output, code = run("--json")

    payload = json.loads(output.strip())
    assert code == 2, "a degraded run must be visible as non-zero"
    assert payload["result"] == "partial_failure"
    assert payload["sources"]["news"]["result"] == FeedResult.FAILED
    assert payload["sources"]["membership"]["result"] == FeedResult.IMPORTED
    assert payload["sources"]["events"]["result"] == FeedResult.IMPORTED

    from apps.events.models import EventSnapshot
    from apps.membership.models import MembershipCountObservation

    assert MembershipCountObservation.objects.filter(is_current=True).count() == 1
    assert EventSnapshot.objects.filter(is_current=True).count() == 1


def test_every_source_failing_returns_one(wire):
    wire(
        membership=MembershipCollectionError("404."),
        news=NewsCollectionError("Vigane XML."),
        events=EventCollectionError("Tühi leht."),
    )

    output, code = run("--json")

    assert code == 1
    assert json.loads(output.strip())["result"] == "failed"


@pytest.mark.parametrize("source", ["membership", "news", "events"])
def test_a_single_source_can_be_run_alone(wire, source):
    wire()

    output, code = run("--source", source, "--json")

    assert code == 0
    payload = json.loads(output.strip())
    assert set(payload["sources"]) == {source}


def test_a_dry_run_publishes_nothing(wire):
    wire()

    output, code = run("--dry-run", "--json")

    assert code == 0
    payload = json.loads(output.strip())
    assert payload["dry_run"] is True

    from apps.events.models import EventSnapshot
    from apps.membership.models import MembershipCountObservation
    from apps.news.models import NewsSnapshot

    assert MembershipCountObservation.objects.count() == 0
    assert NewsSnapshot.objects.count() == 0
    assert EventSnapshot.objects.count() == 0


def test_prose_output_names_each_source(wire):
    wire()

    output, code = run()

    assert code == 0
    for label in ("Liikmeskond", "Uudised", "Sündmused"):
        assert label in output


def test_an_unchanged_repeat_run_still_succeeds(wire):
    wire()
    run("--json")

    output, code = run("--json")

    assert code == 0
    payload = json.loads(output.strip())
    assert payload["result"] == "succeeded"
    for source in ("membership", "news", "events"):
        assert payload["sources"][source]["result"] == FeedResult.UNCHANGED


@pytest.mark.django_db(transaction=True)
def test_a_locked_source_is_reported_and_the_run_is_degraded():
    """A held lock must not be silently ignored."""
    from concurrent.futures import ThreadPoolExecutor

    from django.db import close_old_connections

    def run_events_only():
        close_old_connections()
        try:
            return run("--source", "events", "--json")
        finally:
            close_old_connections()

    with advisory_lock(EVENTS_LOCK):
        with ThreadPoolExecutor(max_workers=1) as executor:
            output, code = executor.submit(run_events_only).result()

    assert code == 3
    payload = json.loads(output.strip())
    assert payload["result"] == "locked"
    assert payload["sources"]["events"]["result"] == "locked"


def test_the_locks_are_distinct_per_source():
    from apps.events.sync import LOCK_NAME as events_lock
    from apps.legal_work.sync import ADVISORY_LOCK_NAMESPACE as legal_lock
    from apps.membership.sync import LOCK_NAME as membership_lock
    from apps.news.sync import LOCK_NAME as news_lock

    assert len({events_lock, membership_lock, news_lock, legal_lock}) == 4


# -- the pages ----------------------------------------------------------


@pytest.fixture
def viewer(client, authenticate_viewer):
    authenticate_viewer(client)
    return client


def test_the_three_routes_resolve():
    assert reverse("membership") == "/liikmeskond/"
    assert reverse("news") == "/uudised/"
    assert reverse("events") == "/sundmused/"


@pytest.mark.parametrize("name", ["membership", "news", "events"])
def test_each_page_requires_viewer_access(client, name):
    response = client.get(reverse(name))

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/sisene/")


# Each page words its own emptiness. The Liikmeskond page is the board report
# now — its source is imported, not connected, so "ei ole veel ühendatud" would
# be the wrong sentence there.
EMPTY_STATE_WORDING = {
    "membership": "Sisemist liikmeskonna aruannet ei ole veel imporditud",
    "news": "ei ole veel ühendatud",
    "events": "ei ole veel ühendatud",
}


@pytest.mark.parametrize("name", ["membership", "news", "events"])
def test_each_page_renders_a_truthful_empty_state(viewer, name):
    response = viewer.get(reverse(name))

    assert response.status_code == 200
    assert EMPTY_STATE_WORDING[name] in response.content.decode()


def test_the_public_member_count_reaches_the_overview(viewer):
    """The directory count leads the headline strip.

    It used to have a section of its own on the Liikmeskond page; the board
    asked for that section to go, so the overview is where the count is read
    now. Nothing about how it is collected or stored changed with it.
    """
    synchronize_membership(collector=collector_returning(membership_collection(3395)))

    body = viewer.get(reverse("home")).content.decode()

    assert "3395" in body
    assert "Liikmeid kokku" in body


def test_the_news_page_lists_items(viewer):
    """The page is an archive now, so the request names the window it wants.

    `news_entry` dates its articles at fixed moments in July 2026. Fetching the
    page bare would ask for the default thirty days and pass only while the
    calendar happened to agree — a test that starts failing on a date nobody
    chose. `periood=koik` asks the question this test is actually about.
    """
    synchronize_news(collector=collector_returning(news_collection(3)))

    body = viewer.get(reverse("news"), {"periood": "koik"}).content.decode()

    assert "Sünteetiline uudis 0" in body
    assert "https://www.koda.ee/et/uudised/synthetic-0" in body


def test_the_public_calendar_is_named_on_the_events_page_but_lists_nothing(viewer):
    """`/sundmused/` is the workbook programme's page.

    The public calendar is a secondary connection there: its state and its own
    count of publicly announced upcoming events are stated, and none of its rows,
    titles, locations or URLs reaches the page.
    """
    synchronize_events(collector=collector_returning(event_collection(3)))

    body = viewer.get(reverse("events")).content.decode()

    assert "Koda.ee avalik kalender" in body
    assert "Avalikus kalendris eelseisvaid sündmusi lähikuul" in body
    assert "Sünteetiline sündmus 0" not in body
    assert "Sünteetiline saal" not in body
    assert "https://www.koda.ee/et/sundmused/synthetic-0" not in body


def test_the_overview_shows_the_two_public_sources_it_still_feeds(viewer):
    """The member directory and the news feed. The public calendar feeds neither
    an overview figure nor the overview's event preview any more."""
    synchronize_membership(collector=collector_returning(membership_collection(3395)))
    synchronize_news(collector=collector_returning(news_collection(3)))
    synchronize_events(collector=collector_returning(event_collection(3)))

    body = viewer.get(reverse("home")).content.decode()

    assert "3395" in body
    assert "Sünteetiline uudis" in body
    assert "Sünteetiline sündmus" not in body


def test_a_failed_check_shows_previous_data_with_a_warning(viewer):
    """A failed check is disclosed, and the last good number is not withdrawn.

    Both now happen on the overview: the count is in the headline strip and the
    stale source is counted in the connection strip at the foot of the page. The
    Liikmeskond page no longer carries the directory's connection state, because
    it no longer carries the directory.
    """
    synchronize_membership(collector=collector_returning(membership_collection(3395)))
    synchronize_membership(
        collector=collector_raising(MembershipCollectionError("Sünteetiline sisemine veateade."))
    )

    body = viewer.get(reverse("home")).content.decode()

    assert "3395" in body, "the previous good number must still be shown"
    assert "Vananenud: 1" in body, "the failed check is disclosed"
    assert "Sünteetiline sisemine veateade" not in body, "no exception detail may reach a viewer"


def visible_text(response) -> str:
    """The page's prose, with markup and attribute values removed.

    Attribute values are stripped because a CSRF token is random and will
    eventually contain any short letter sequence — matching raw HTML would make
    a wording assertion flaky rather than meaningful.
    """
    html = response.content.decode()
    without_attributes = re.sub(r"<[^>]*>", " ", html)
    return " ".join(without_attributes.split()).lower()


@pytest.mark.parametrize("route", ["home", "membership", "news", "events"])
def test_no_year_to_date_wording_appears_anywhere(viewer, route):
    synchronize_membership(collector=collector_returning(membership_collection(3395)))

    text = visible_text(viewer.get(reverse(route)))

    for phrase in ("uusi liikmeid", "sel aastal", "lisandunud", "year to date"):
        assert phrase not in text
    assert not re.search(r"\bytd\b", text)


@pytest.mark.parametrize("route", ["home", "membership", "news", "events"])
def test_teataja_appears_nowhere(viewer, route):
    assert "teataja" not in visible_text(viewer.get(reverse(route)))


def test_the_navigation_marks_the_new_routes_available():
    from apps.dashboard.navigation import NAVIGATION

    by_key = {item.key: item for item in NAVIGATION}
    for key in ("membership", "news", "events"):
        assert by_key[key].is_available is True


def test_external_links_are_safe(viewer):
    """A link off the application opens in a new tab, and says so.

    `target="_blank"` without `rel="noopener"` hands the opened page a reference
    back to this one, so the two travel together. The visually hidden note is the
    other half: a new tab that only sighted users are told about is a new tab a
    screen-reader user has to discover by finding the back button gone.
    """
    synchronize_news(collector=collector_returning(news_collection(2)))

    body = viewer.get(reverse("news")).content.decode()

    assert 'target="_blank" rel="noopener noreferrer"' in body
    assert "uuel vahelehel" in body
    assert body.count('target="_blank"') == body.count('rel="noopener noreferrer"')


def test_the_pages_keep_the_strict_csp(viewer):
    response = viewer.get(reverse("news"))

    policy = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in policy
    assert "unsafe-inline" not in policy
    assert "unsafe-eval" not in policy
