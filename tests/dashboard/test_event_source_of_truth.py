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

from apps.dashboard import freshness, overview
from apps.event_programme.selectors import EventProgrammeSummary
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


def test_the_overview_event_cell_reads_the_workbook(viewer, published_programme):
    """Each count is asserted with its value, not just its label.

    The synthetic programme has one event five days out, and two behind: the one
    still running and the one ten days ago. A label-only assertion would pass on
    a cell that had lost its figures.
    """
    page = text_of(viewer.get(reverse("home")))

    assert "sündmusi järgmise 30 päeva jooksul 1" in page
    assert "sündmusi eelmise 30 päeva jooksul 2" in page


def test_the_overview_names_the_programme_as_its_event_source():
    assert overview.SOURCE_EVENTS == "Sündmuste programm"
    assert "kalender" not in overview.SOURCE_EVENTS.casefold()


def test_the_overview_takes_no_event_figure_from_the_public_calendar(viewer):
    """The public calendar published, the workbook not. The cell stays empty."""
    synchronize_events(collector=collector_returning(event_collection(3)))

    response = viewer.get(reverse("home"))
    page = text_of(response)
    preview = section(response, "section-events")

    assert "sündmusi järgmise 30 päeva jooksul" not in page
    assert "sündmusi eelmise 30 päeva jooksul" not in page
    assert "Sünteetiline sündmus 0" not in strip_tags(preview), (
        "no public-calendar event may reach the overview preview"
    )
    assert "Andmeallikas ei ole veel ühendatud." in strip_tags(preview)


def test_the_overview_preview_shows_the_programme_and_links_only_what_the_workbook_linked(
    viewer, published_programme
):
    response = viewer.get(reverse("home"))
    preview = section(response, "section-events")

    # The upcoming linked event carries an anchor and the project's wording.
    assert "Sünteetiline tulev koolitus" in strip_tags(preview)
    assert "(koda.ee, avaneb uuel vahelehel)" in preview
    # The ongoing event has no workbook link, so its title is plain text.
    assert "Sünteetiline käimasolev sündmus" in strip_tags(preview)
    assert 'href="https://www.koda.ee/et/sundmused/sunteetiline-programmi-sundmus-tulev"' in preview


def test_a_stale_programme_keeps_its_figures_on_the_overview(viewer, published_programme):
    """A failed later check discloses itself and withdraws nothing.

    The disclosure moved to `/dashboard/varskus/` in #104; the figures staying
    put on the overview is the half that must never move.
    """
    from apps.event_programme.public_download import PublicDownloadError

    synchronize_public_workbook(
        downloader=FakeDownloader(error=PublicDownloadError("Sünteetiline tõrge."))
    )

    page = text_of(viewer.get(reverse("home")))
    freshness = text_of(viewer.get(reverse("dashboard-freshness")))

    assert "Vananenud: 1" in freshness
    assert "sündmusi järgmise 30 päeva jooksul 1" in page, "the figures are not withdrawn"
    assert "sündmusi eelmise 30 päeva jooksul 2" in page
    assert "Sünteetiline tõrge" not in page, "no failure detail may reach a viewer"
