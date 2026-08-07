"""The overview once sources are actually connected.

`test_overview.py` covers the page with nothing connected, where its job is to
show no numbers at all. This module covers the opposite: that every figure the
board reads is the one the source published, that a comparison names its own
baseline, and that a part with no source still says so on a page full of data.

Every value is synthetic and built here. Nothing reads a real workbook, calls
Koda.ee or touches the approved membership package.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.core.files import File
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

from apps.events.sync import synchronize_events
from apps.legal_work.bootstrap import ensure_legal_work_source
from apps.legal_work.importer import import_artifact
from apps.membership.history_import import import_history_package
from apps.membership.models import MembershipCountObservation
from apps.membership.selectors import get_current_membership_observation
from apps.membership.sync import synchronize_membership
from apps.news.collector import NewsCollectionError
from apps.news.sync import synchronize_news
from apps.sources.services import register_artifact
from tests.koda.conftest import (
    collector_raising,
    collector_returning,
    event_collection,
    membership_collection,
    news_collection,
)
from tests.legal_work.workbook_factory import synthetic_row, write_workbook
from tests.membership.package_factory import build_package

pytestmark = pytest.mark.django_db

TODAY = dt.date.today()


@pytest.fixture
def viewer(client, authenticate_viewer):
    authenticate_viewer(client)
    return client


@pytest.fixture
def imported_internal_history(db, tmp_path):
    """The Chamber's own board-report history, from a synthetic package."""
    return import_history_package(build_package(tmp_path / "package.zip"), dry_run=False)


def legal_work_rows() -> list[list]:
    """Three rows chosen to exercise every count the overview shows.

    One arrived inside the activity window and has a deadline three days out;
    one was sent inside the window; one is old enough to be outside it, so a
    window that quietly ignored its bounds would be visible as a wrong count.
    """
    return [
        synthetic_row(
            record_id="SYN-0001",
            topic="Sünteetiline kiireloomuline teema",
            received_date=TODAY - dt.timedelta(days=5),
            deadline_date=TODAY + dt.timedelta(days=3),
            is_open=True,
            source_row=2,
        ),
        synthetic_row(
            record_id="SYN-0002",
            topic="Sünteetiline saadetud teema",
            received_date=TODAY - dt.timedelta(days=10),
            deadline_date=TODAY - dt.timedelta(days=1),
            sent_date=TODAY - dt.timedelta(days=2),
            sent_status="sent",
            is_open=False,
            source_row=3,
        ),
        synthetic_row(
            record_id="SYN-0003",
            topic="Sünteetiline vana teema",
            received_date=TODAY - dt.timedelta(days=200),
            deadline_date=TODAY + dt.timedelta(days=400),
            is_open=True,
            source_row=4,
        ),
    ]


@pytest.fixture
def legal_work_snapshot(db, tmp_path):
    path = write_workbook(tmp_path / "synthetic.xlsx", rows=legal_work_rows())
    source = ensure_legal_work_source()
    with path.open("rb") as handle:
        artifact = register_artifact(
            source=source,
            upload=File(handle, name="dashkoda_oigusloome.xlsx"),
            original_name="dashkoda_oigusloome.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    return import_artifact(artifact, dry_run=False).snapshot


def body(response) -> str:
    return response.content.decode()


def text_of(response) -> str:
    return " ".join(strip_tags(body(response)).split())


def section(response, heading_id: str) -> str:
    """One `<section>` of the page, by the id its heading carries.

    Several assertions are about where something appears rather than whether it
    appears at all — "pärast" is ordinary Estonian and turns up in half the
    empty states, so a whole-page search would prove nothing.
    """
    return body(response).split(f'aria-labelledby="{heading_id}"')[1].split("</section>")[0]


def kpi_strip(response) -> str:
    return section(response, "section-kpi")


def backdate_current_observation(*, days: int) -> None:
    """Age the published member count so a window has something behind it.

    A recorded observation is immutable through `save()`, deliberately. A test
    that needs a reading from last month has no other way to make one: the sync
    stamps `timezone.now()`, and freezing the clock would move the window too.
    `QuerySet.update()` writes the column directly and is used here only.
    """
    current = get_current_membership_observation()
    MembershipCountObservation.objects.filter(pk=current.pk).update(
        observed_at=current.observed_at - dt.timedelta(days=days)
    )


# -- legal work ---------------------------------------------------------


def test_the_open_count_and_activity_come_from_the_snapshot(viewer, legal_work_snapshot):
    response = viewer.get(reverse("home"))
    strip = " ".join(strip_tags(kpi_strip(response)).split())

    assert legal_work_snapshot.open_record_count == 2
    # Two open topics; two arrivals and one send inside the window, with the
    # 200-day-old row deliberately outside it. Every count is in the module's
    # own headline cell, and each states the period it was measured over.
    assert "teemasid töös 2" in strip
    assert "uusi teemasid 30 päevaga 2" in strip
    assert "välja läinud teemasid 30 päevaga 1" in strip
    assert "Sünteetiline kiireloomuline teema" in body(response)


def test_the_card_offers_exactly_two_lists_with_work_in_hand_leading(viewer, legal_work_snapshot):
    """Töös then Välja läinud, and nothing else.

    Töös leads because it is a state rather than an event: a board member
    opening the page asks what is on the table before asking what has already
    left. Arrivals used to sit between them, which read as a third lifecycle
    state; they are a way of sorting active work, so they are Töös rows now.
    """
    card = section(viewer.get(reverse("home")), "section-legislation")
    tabs = [card.index(f'id="tab-{name}"') for name in ("open", "sent")]

    assert tabs == sorted(tabs), "work in hand leads, departures follow"
    # The panel bound to the first tab is the one Alpine shows on load.
    assert (
        'id="panel-open" role="tabpanel" aria-labelledby="tab-open" x-show="firstSelected"' in card
    )
    assert "tähtaeg" in card
    assert "välja" in card


def test_the_arrivals_tab_is_gone_entirely(viewer, legal_work_snapshot):
    """Not hidden, not renamed, not left behind as empty markup."""
    card = section(viewer.get(reverse("home")), "section-legislation")

    assert "Viimased sisse" not in card
    assert 'id="tab-received"' not in card
    assert 'id="panel-received"' not in card
    assert "thirdSelected" not in card, "no third tab state may survive"
    assert card.count('role="tab"') - card.count('role="tabpanel"') == 2
    assert card.count('role="tabpanel"') == 2
    assert 'x-data="tabPair"' in card


def test_every_tab_still_maps_to_exactly_one_panel(viewer, legal_work_snapshot):
    """Accessibility survives the removal: two tabs, two panels, paired."""
    card = section(viewer.get(reverse("home")), "section-legislation")

    for name in ("open", "sent"):
        assert f'id="tab-{name}"' in card
        assert f'aria-controls="panel-{name}"' in card
        assert f'id="panel-{name}"' in card
        assert f'aria-labelledby="tab-{name}"' in card
    assert 'role="tablist"' in card


def test_a_recently_received_active_topic_is_in_toos(viewer, legal_work_snapshot):
    """The one thing removing the tab must not do is hide active work.

    Arrivals were their own list; now the only place an active record can
    appear on this card is Töös, so an open record must be there whatever its
    received date.
    """
    card = section(viewer.get(reverse("home")), "section-legislation")
    panel = card.split('id="panel-open"', 1)[1].split('id="panel-sent"', 1)[0]

    assert "Sünteetiline kiireloomuline teema" in panel


def test_a_sent_topic_never_appears_in_toos(legal_work_snapshot):
    """Töös is the active population, and sent work has left it."""
    from apps.dashboard.overview import LEGAL_ACTIVE_LIMIT
    from apps.legal_work.selectors import get_latest_sent_items, get_open_items_by_deadline

    active = list(get_open_items_by_deadline(legal_work_snapshot, limit=LEGAL_ACTIVE_LIMIT))
    sent = list(get_latest_sent_items(legal_work_snapshot))

    assert active, "the fixture must have active work for this to mean anything"
    for item in active:
        assert item.is_open
    assert not ({i.pk for i in active} & {i.pk for i in sent})


def test_toos_lists_each_record_once(legal_work_snapshot):
    from apps.dashboard.overview import LEGAL_ACTIVE_LIMIT
    from apps.legal_work.selectors import get_open_items_by_deadline

    ids = [i.pk for i in get_open_items_by_deadline(legal_work_snapshot, limit=LEGAL_ACTIVE_LIMIT)]

    assert len(ids) == len(set(ids))


def test_an_active_topic_without_a_deadline_is_not_lost(legal_work_snapshot):
    """It trails the dated ones rather than dropping out of the population."""
    from apps.legal_work.selectors import get_open_items_by_deadline

    active = list(get_open_items_by_deadline(legal_work_snapshot, limit=None))
    dated = [i for i in active if i.deadline_date]
    undated = [i for i in active if not i.deadline_date]

    assert len(active) == len(dated) + len(undated)
    if undated and dated:
        order = [i.pk for i in active]
        assert order.index(undated[0].pk) > order.index(dated[-1].pk)


def test_each_card_lists_enough_rows_to_stand_level_with_its_neighbour():
    """A grid row is as tall as its tallest card, so the two cards in a row are
    tuned together: a card listing fewer rows than the one beside it leaves
    space that is already being paid for.

    Pinned as limits rather than as rendered row counts because the synthetic
    fixtures hold fewer records than any of these numbers.
    """
    from apps.dashboard.overview import (
        EVENTS_PREVIEW_LIMIT,
        LEGAL_ACTIVE_LIMIT,
        LEGAL_PREVIEW_LIMIT,
        NEWS_PREVIEW_LIMIT,
    )

    # Row one: the sent list keeps the tuned preview height.
    assert LEGAL_PREVIEW_LIMIT == 7
    # Töös absorbed the arrivals tab, so it carries what two tabs of seven could
    # jointly surface. Shrinking this is how an active record becomes harder to
    # find than it was before the simplification.
    assert LEGAL_ACTIVE_LIMIT == 2 * LEGAL_PREVIEW_LIMIT
    # Row two: two cards of the same shape, kept level with each other.
    assert EVENTS_PREVIEW_LIMIT == NEWS_PREVIEW_LIMIT == 5


def test_a_topic_in_the_card_is_plain_text_until_a_source_gives_it_an_address(
    viewer, legal_work_snapshot
):
    """No legal record has a public URL: the workbook has no address column and
    is read-only to this application. The rows say so by not being links."""
    card = section(viewer.get(reverse("home")), "section-legislation")
    lists = card.split('class="divide-y divide-border"', 1)[1]

    assert "Sünteetiline kiireloomuline teema" in lists
    assert "<a " not in lists, "a topic must not link to nowhere"


def test_each_legal_count_links_to_the_section_that_lists_its_rows(viewer, legal_work_snapshot):
    """Three counts, three destinations, and none of them shared.

    The board reads a count and wants the rows behind it. Pointing all three at
    the top of the Õigusloome page would make the link a page link wearing a
    count's label; each one lands on the section that lists exactly what it
    counted. `tests/legal_work/test_views.py` holds the page to those anchors.
    """
    page_url = reverse("legal-work")
    strip = kpi_strip(viewer.get(reverse("home")))

    assert f'href="{page_url}#section-open" class="dk-link-quiet">teemasid töös</a>' in strip
    assert f'href="{page_url}#section-received" class="dk-link-quiet">uusi teemasid' in strip
    assert f'href="{page_url}#section-sent" class="dk-link-quiet">välja läinud teemasid' in strip
    # Three links in the whole strip and no more. The Sündmused counts stay
    # plain: that page lists the programme, not the two windows this strip
    # counts, and a link landing on a different set of rows than the number
    # describes is worse than no link.
    assert strip.count("dk-link-quiet") == 3


def test_the_overview_no_longer_carries_a_deadline_section(viewer, legal_work_snapshot):
    """The board asked for the attention block to go.

    The deadlines themselves are unchanged — `get_upcoming_deadlines` and the
    Õigusloome page still work through them — but the overview no longer
    repeats them above the fold.
    """
    page = text_of(viewer.get(reverse("home")))

    assert "Juhatuse tähelepanu" not in page
    assert "arvamuse tähtaeg" not in page


# -- public membership --------------------------------------------------


def test_the_member_total_states_its_movement_over_the_stated_window(viewer):
    synchronize_membership(collector=collector_returning(membership_collection(3400)))
    backdate_current_observation(days=40)
    synchronize_membership(collector=collector_returning(membership_collection(3396)))

    strip = kpi_strip(viewer.get(reverse("home")))

    assert "3396" in strip
    # The baseline is the last reading before the window opened, and the cell
    # names the window it measured rather than leaving the reader to guess.
    assert "-4" in strip
    assert "↓" in strip
    assert "viimase 30 päeva jooksul" in strip


def test_every_as_of_row_states_one_date_and_never_a_time(viewer, legal_work_snapshot):
    """One date shape under every figure, and no clock time in any of them.

    The strip used to render three shapes side by side — a full timestamp for the
    member count, a long-form date for the legal figure, another timestamp for
    events — because it was the one caller passing `as_of` to the card
    unformatted, so each cell rendered whatever its own value type produced.

    A date here describes the data. When a collector happened to run is a
    different fact and lives in "Viimati kontrollitud", which keeps its time.
    """
    synchronize_membership(collector=collector_returning(membership_collection(3400)))

    page = text_of(viewer.get(reverse("home")))
    today = timezone.localdate()
    today_text = f"{today.day}.{today:%m.%y}"

    stated = re.findall(r"[Ss]eisuga:?\s+(\S+)", page)
    assert stated, "the overview states no as-of date at all"
    for value in stated:
        # An em dash is the documented empty state: this figure has no source
        # yet, so it names no date rather than inventing one.
        if value == "—":
            continue
        assert re.fullmatch(r"\d{1,2}\.\d{2}\.\d{2}", value), f"not a plain date: {value!r}"

    assert today_text in stated
    assert not re.search(r"[Ss]eisuga:?\s+\S+\s+\d{1,2}:\d{2}", page)


def test_a_reading_with_no_baseline_that_old_shows_no_change(viewer):
    """Two readings a moment apart do not make a month's movement."""
    synchronize_membership(collector=collector_returning(membership_collection(3400)))
    synchronize_membership(collector=collector_returning(membership_collection(3396)))

    strip = kpi_strip(viewer.get(reverse("home")))

    assert "3396" in strip
    # Nothing predates the window, so the month's difference is unknown rather
    # than the -4 that happened inside a single test run.
    assert "↑" not in strip
    assert "↓" not in strip
    assert "→" not in strip
    assert "viimase 30 päeva jooksul" not in strip


def test_a_first_ever_reading_shows_no_change_it_cannot_know(viewer):
    synchronize_membership(collector=collector_returning(membership_collection(3400)))

    strip = kpi_strip(viewer.get(reverse("home")))

    assert "3400" in strip
    # A first observation has nothing behind it, so the difference is unknown
    # rather than zero. No direction marker may appear at all.
    assert "↑" not in strip
    assert "↓" not in strip
    assert "→" not in strip


def test_the_two_membership_sources_are_never_merged(viewer, imported_internal_history):
    """Each total is stated once, and the two are never drawn together.

    The board asked for the per-figure source lines and the explanatory note to
    go, so the overview no longer argues the point in prose. What still has to
    hold structurally is that the directory count appears only in the headline
    strip and the board report's own figures only in their card. Naming the
    sources is the Liikmeskond page's job now, and `test_membership_page.py`
    holds it to that.
    """
    synchronize_membership(collector=collector_returning(membership_collection(3400)))

    response = viewer.get(reverse("home"))
    page = body(response)
    card = section(response, "section-membership")

    # The directory total is stated once, in the headline strip. Repeating it
    # inside the board report's card is what let a reader read two definitions
    # as one number.
    assert "3400" in kpi_strip(response)
    assert "Liikmeid kataloogis" not in page
    assert "Koda.ee liikmekataloog" not in card, "the directory is not a source of this card"
    assert "Tasunud liikmeid" in card, "the card holds the report's own figures"


def test_fee_collection_sits_with_the_counts_it_was_read_beside(viewer, imported_internal_history):
    """The percentage belongs to the board report, so it lives in its card.

    In the headline strip it sat between a directory count and a calendar, four
    cells with nothing in common; the amounts behind it are the same report's
    and the reader needs them side by side.
    """
    response = viewer.get(reverse("home"))
    card = " ".join(strip_tags(section(response, "section-membership")).split())

    assert "Liikmemaksude laekumine" in card
    assert "Liikmemaksude laekumine" not in strip_tags(kpi_strip(response))
    # The euros behind the percentage, grouped and in the report's own currency.
    assert "€" in card


def test_the_trend_offers_only_the_windows_this_history_can_fill(viewer, imported_internal_history):
    """The synthetic package spans a year and five days.

    Six and twelve months sit inside that; two years is the first window that
    covers all of it and is offered for exactly that reason. Three years would
    draw the identical line under a different name, which reads as a button that
    did nothing.
    """
    card = section(viewer.get(reverse("home")), "section-membership")

    assert "6 kuud" in card
    assert "12 kuud" in card
    assert "2 aastat" in card
    assert "3 aastat" not in card


def test_a_narrower_window_draws_less_without_moving_the_latest_figures(
    viewer, imported_internal_history
):
    """The range control changes how much history is drawn and nothing else.

    A headline figure that shifted when a reader asked for a shorter line would
    be answering a question nobody put: the three figures above the chart are
    the most recent report either way.
    """
    wide = section(viewer.get(reverse("home"), {"vahemik": "24"}), "section-membership")
    narrow = section(viewer.get(reverse("home"), {"vahemik": "6"}), "section-membership")

    # Two observations a year apart: the wide window draws both, and the short
    # one is left with a single point, which is not a trend and is not drawn.
    assert wide.count("<rect") == 2
    assert "Trendi kuvamiseks on vaja vähemalt kahte vaatlust." in narrow
    assert "<rect" not in narrow
    # Both still state the same report, on the same date.
    assert "15.01.25" in wide
    assert "15.01.25" in narrow


def test_an_unknown_range_renders_the_default_rather_than_failing(
    viewer, imported_internal_history
):
    response = viewer.get(reverse("home"), {"vahemik": "'; DROP TABLE"})

    assert response.status_code == 200
    assert "DROP TABLE" not in body(response)


# -- feeds --------------------------------------------------------------


def test_news_reaches_its_card(viewer):
    synchronize_news(collector=collector_returning(news_collection(3)))

    page = body(viewer.get(reverse("home")))

    assert "Sünteetiline uudis" in page


def test_the_public_event_calendar_reaches_no_card(viewer):
    """It is collected, and it is not what the overview's event figures read.

    The event cell and the event preview come from the canonical workbook
    programme; `tests/dashboard/test_event_source_of_truth.py` covers that side.
    """
    synchronize_events(collector=collector_returning(event_collection(3)))

    page = body(viewer.get(reverse("home")))

    assert "Sünteetiline sündmus" not in page
    assert "sündmusi järgmise 30 päeva jooksul" not in page


def test_a_failed_check_is_still_disclosed_and_keeps_the_last_good_data(viewer):
    """The attention section is gone; the disclosure is not.

    The connection strip at the foot of the page counts the stale sources, so a
    failed check is still stated where a reader can see it, and the last data
    that did arrive stays on the page rather than being withdrawn.

    The news feed carries this now: the public event calendar is no longer one of
    the four sources the shell row speaks for.
    """
    synchronize_news(collector=collector_returning(news_collection(3)))
    synchronize_news(collector=collector_raising(NewsCollectionError("Sünteetiline viga.")))

    page = text_of(viewer.get(reverse("home")))

    assert "Vananenud: 1" in page
    assert "Sünteetiline viga" not in page, "no exception detail may reach a viewer"
    assert "Sünteetiline uudise pealkiri" in page or "Sünteetiline uudis" in page, (
        "the last good data must still be shown"
    )


# -- what is not connected ----------------------------------------------


def test_unconnected_parts_still_say_so_on_a_page_full_of_data(viewer, legal_work_snapshot):
    synchronize_news(collector=collector_returning(news_collection(3)))
    synchronize_membership(collector=collector_returning(membership_collection(3400)))

    page = body(viewer.get(reverse("home")))

    assert "Kanalite statistika" in page
    assert "Meediakajastused" in page
    # Website visits have no source at all and say exactly that.
    assert "Kodulehe külastused" in page
    assert "Google Analytics ei ole ühendatud." in page
    # The five channels that *can* hold a value have none entered yet, which is a
    # different statement and gets different wording.
    assert page.count("Andmed puuduvad.") >= 5
    assert "Andmeallikas ei ole veel ühendatud." in page


def test_an_unconnected_source_contributes_no_zero(viewer):
    """Nothing is connected, so no count may appear — least of all a zero."""
    page = text_of(viewer.get(reverse("home")))

    assert "teemasid töös" not in page
    assert "uusi teemasid" not in page
    assert "sündmusi järgmise" not in page
    assert "sündmusi eelmise" not in page


# The "each summary is read exactly once" guarantee moved to
# `test_page_summary_queries.py`, which holds it for the overview and for every
# module page in one place rather than only for this one.
