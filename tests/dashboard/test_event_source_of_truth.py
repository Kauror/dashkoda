"""Which event source the dashboard speaks for.

The canonical Excel programme is the dashboard's event source. The public Koda.ee
calendar keeps collecting on its own schedule and keeps its own snapshots, but it
no longer supplies a dashboard figure, a total or a link.

These tests fix both halves: the workbook's counts reach the overview and the
shell, and publishing the public calendar alone changes neither.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils.html import strip_tags

from apps.dashboard import executive, freshness
from apps.event_programme.executive import get_events_executive
from apps.event_programme.selectors import (
    EventProgrammeSummary,
    get_event_programme_summary,
)
from apps.event_programme.sync import synchronize_public_workbook
from apps.events.models import EventSnapshot
from apps.events.selectors import EventSummary
from apps.events.sync import synchronize_events
from tests.event_programme.conftest import FakeDownloader, synthetic_programme
from tests.event_programme.workbook_factory import build_workbook
from tests.koda.conftest import collector_returning, event_collection

pytestmark = pytest.mark.django_db


@pytest.fixture
def viewer(client, authenticate_viewer):
    authenticate_viewer(client)
    return client


@pytest.fixture
def published_programme(db, tmp_path):
    """The canonical programme, published through its real synchronisation."""
    path = build_workbook(tmp_path / "programme.xlsx", rows=synthetic_programme())
    return synchronize_public_workbook(downloader=FakeDownloader(path))


def text_of(response) -> str:
    return " ".join(strip_tags(response.content.decode()).split())


def section(response, heading_id: str) -> str:
    return (
        response.content.decode().split(f'aria-labelledby="{heading_id}"')[1].split("</section>")[0]
    )


# -- the shell freshness row --------------------------------------------


def test_the_shell_speaks_for_the_programme_and_not_the_public_calendar():
    registered = {summary_class for summary_class, _loader in freshness._SUMMARY_SOURCES}

    assert EventProgrammeSummary in registered
    assert EventSummary not in registered


def test_the_shell_denominator_stays_at_four():
    """Two event collectors are still one business domain.

    The event domain has a canonical feed and a supplementary public one. Counting
    both would tell a board member the dashboard covers five subjects when it
    covers four.
    """
    assert len(freshness._SUMMARY_SOURCES) == 4
    assert freshness.current_freshness().total_sources == 4


def test_the_programme_makes_the_event_domain_connected(published_programme):
    state = freshness.current_freshness()

    assert state.connected_sources == 1
    assert state.message == "Ühendatud andmeallikaid: 1/4."


def test_the_public_calendar_alone_connects_nothing_in_the_shell():
    synchronize_events(collector=collector_returning(event_collection(3)))

    state = freshness.current_freshness()

    assert state.connected_sources == 0
    assert EventSnapshot.objects.filter(is_current=True).count() == 1, (
        "the public collector still publishes its own snapshot"
    )


def test_a_page_may_hand_back_the_programme_summary_it_already_read(published_programme):
    from apps.event_programme.selectors import get_event_programme_summary

    without = freshness.current_freshness()
    with_preloaded = freshness.current_freshness(get_event_programme_summary())

    assert with_preloaded.connected_sources == without.connected_sources
    assert with_preloaded.total_sources == without.total_sources
    assert with_preloaded.stale_sources == without.stale_sources


# -- the overview -------------------------------------------------------


def test_the_kaasamine_pillar_reads_the_workbook(viewer, published_programme):
    """Each count is asserted with its value, not just its label.

    The synthetic programme has one event five days out, and two behind: the one
    still running and the one ten days ago. A label-only assertion would pass on
    a pillar that had lost its figures.
    """
    # The `Kaasamine` card left the overview on 2026-08-16, so the figure is
    # asserted where it is still produced. The rule this protects is unchanged:
    # the pillar reads the programme workbook, and a label-only assertion would
    # pass on one that had lost its figures.
    pillar = executive._events_pillar(get_events_executive(get_event_programme_summary()))

    assert pillar.label == "Kaasamine"
    assert "Algab 30 päeva jooksul 1" in f"{pillar.headline.label} {pillar.headline.value}"
    assert "Kaasamine" not in text_of(viewer.get(reverse("home")))


def test_the_overview_names_the_programme_as_its_event_source():
    assert executive.SOURCE_EVENTS == "Sündmuste programm"
    assert "kalender" not in executive.SOURCE_EVENTS.casefold()


def test_the_overview_takes_no_event_figure_from_the_public_calendar(viewer):
    """The public calendar published, the workbook not. The pillar stays empty.

    Empty means the unavailable note, never a zero: nobody counted no events.
    """
    synchronize_events(collector=collector_returning(event_collection(3)))

    response = viewer.get(reverse("home"))
    page = text_of(response)

    assert "Algab 30 päeva jooksul" not in page
    assert "Sünteetiline sündmus 0" not in page, (
        "no public-calendar event may reach the executive overview"
    )
    assert executive.NO_SOURCE_NOTE in page


def test_the_timeline_shows_the_programme_and_links_only_what_the_workbook_linked(
    viewer, published_programme
):
    """The upcoming event reaches the shared thirty-day timeline.

    The overview no longer previews a list of events — that moved to Sündmused —
    but a scheduled event inside the horizon is dated work and belongs on the
    timeline, with its public page where the matcher resolved one.
    """
    response = viewer.get(reverse("home"))
    body = response.content.decode()

    assert "Sünteetiline tulev koolitus" in strip_tags(body)
    assert "(avaneb uuel vahelehel)" in body
    assert 'href="https://www.koda.ee/et/sundmused/sunteetiline-programmi-sundmus-tulev"' in body


def test_a_stale_programme_keeps_its_figures_on_the_overview(viewer, published_programme):
    """A failed later check discloses itself and withdraws nothing.

    The disclosure is on the shell freshness row and in `Andmete seis`; the
    figures staying put on the overview is the half that must never move.
    """
    from apps.event_programme.public_download import PublicDownloadError

    synchronize_public_workbook(
        downloader=FakeDownloader(error=PublicDownloadError("Sünteetiline tõrge."))
    )

    page = text_of(viewer.get(reverse("home")))
    freshness = text_of(viewer.get(reverse("dashboard-freshness")))
    pillar = executive._events_pillar(get_events_executive(get_event_programme_summary()))

    assert "Vananenud: 1" in freshness
    # Asserted on the builder since the card left the overview on 2026-08-16.
    # The rule is the one that matters: a failed sync must not withdraw figures
    # that were successfully imported earlier.
    assert "Algab 30 päeva jooksul 1" in f"{pillar.headline.label} {pillar.headline.value}", (
        "the figures are not withdrawn"
    )
    assert "Sünteetiline tõrge" not in page, "no failure detail may reach a viewer"
