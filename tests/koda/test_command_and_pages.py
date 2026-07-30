"""The sync_koda_public command, and the pages the three feeds render.

No HTTP happens here: the command's synchronise functions are patched with
synthetic collectors.
"""

from __future__ import annotations

import json
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


@pytest.mark.parametrize("name", ["membership", "news", "events"])
def test_each_page_renders_a_truthful_empty_state(viewer, name):
    response = viewer.get(reverse(name))

    assert response.status_code == 200
    assert "ei ole veel ühendatud" in response.content.decode()


def test_the_membership_page_shows_the_count(viewer):
    synchronize_membership(collector=collector_returning(membership_collection(3395)))

    body = viewer.get(reverse("membership")).content.decode()

    assert "3395" in body
    assert "Liikmeid kokku" in body


def test_the_news_page_lists_items(viewer):
    synchronize_news(collector=collector_returning(news_collection(3)))

    body = viewer.get(reverse("news")).content.decode()

    assert "Sünteetiline uudis 0" in body
    assert "https://www.koda.ee/et/uudised/synthetic-0" in body


def test_the_events_page_lists_upcoming_events(viewer):
    synchronize_events(collector=collector_returning(event_collection(3)))

    body = viewer.get(reverse("events")).content.decode()

    assert "Sünteetiline sündmus 0" in body
    assert "Sünteetiline saal" in body


def test_the_overview_shows_all_three_sources(viewer):
    synchronize_membership(collector=collector_returning(membership_collection(3395)))
    synchronize_news(collector=collector_returning(news_collection(3)))
    synchronize_events(collector=collector_returning(event_collection(3)))

    body = viewer.get(reverse("home")).content.decode()

    assert "3395" in body
    assert "Sünteetiline uudis" in body
    assert "Sünteetiline sündmus" in body


def test_a_failed_check_shows_previous_data_with_a_warning(viewer):
    synchronize_membership(collector=collector_returning(membership_collection(3395)))
    synchronize_membership(collector=collector_raising(MembershipCollectionError("404.")))

    body = viewer.get(reverse("membership")).content.decode()

    assert "3395" in body, "the previous good number must still be shown"
    assert "Viimane kontroll ebaõnnestus" in body
    assert "404" not in body, "no exception detail may reach a viewer"


@pytest.mark.parametrize("route", ["home", "membership", "news", "events"])
def test_no_year_to_date_wording_appears_anywhere(viewer, route):
    synchronize_membership(collector=collector_returning(membership_collection(3395)))

    body = viewer.get(reverse(route)).content.decode().lower()

    for forbidden in ("uusi liikmeid", "sel aastal", "ytd", "year to date", "lisandunud"):
        assert forbidden not in body


@pytest.mark.parametrize("route", ["home", "membership", "news", "events"])
def test_teataja_appears_nowhere(viewer, route):
    body = viewer.get(reverse(route)).content.decode().lower()

    assert "teataja" not in body


def test_the_navigation_marks_the_new_routes_available():
    from apps.dashboard.navigation import NAVIGATION

    by_key = {item.key: item for item in NAVIGATION}
    for key in ("membership", "news", "events"):
        assert by_key[key].is_available is True


def test_external_links_are_safe(viewer):
    synchronize_news(collector=collector_returning(news_collection(2)))

    body = viewer.get(reverse("news")).content.decode()

    assert 'rel="noopener noreferrer"' in body
    assert "target=" not in body, "the design system has no accessible new-tab pattern"


def test_the_pages_keep_the_strict_csp(viewer):
    response = viewer.get(reverse("news"))

    policy = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in policy
    assert "unsafe-inline" not in policy
    assert "unsafe-eval" not in policy
