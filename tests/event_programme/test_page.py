"""The Sündmused page: what it shows, what it links and what it refuses to guess.

Every fixture is published through the real synchronisation path, so the page is
rendered from the same rows production would hold.

Three properties are the point of this module:

- the workbook programme is the page's primary content, including its history;
- a title becomes a link only when the workbook supplied one — no title match, no
  date match, no service-code inference and no search;
- an undated event stays a record, is disclosed, and is reachable.
"""

from __future__ import annotations

import datetime as dt
import html
import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

from apps.event_programme.models import EventProgrammeItem
from apps.event_programme.selectors import PAGE_SIZE, get_current_event_programme_snapshot

from .conftest import SYNTHETIC_URL, programme_years, synthetic_programme
from .workbook_factory import default_control, synthetic_row

pytestmark = pytest.mark.django_db

PAGE_URL = "/sundmused/"

PROGRAMME_ROWS = 9

# Kuupäev, Sündmus, Silt, Seisund — the name is the second cell.
NAME_CELL = 1


@pytest.fixture
def viewer(client, authenticate_viewer):
    authenticate_viewer(client)
    return client


@pytest.fixture
def programme(publish_programme):
    publish_programme(rows=synthetic_programme())
    return get_current_event_programme_snapshot()


def body(response) -> str:
    return response.content.decode()


def text_of(response) -> str:
    return " ".join(strip_tags(body(response)).split())


def section(response, heading_id: str) -> str:
    return body(response).split(f'aria-labelledby="{heading_id}"')[1].split("</section>")[0]


def programme_section(response) -> str:
    return section(response, "section-programme")


def table_body(response) -> str:
    page = programme_section(response)
    if "<tbody>" not in page:
        return ""
    return page.split("<tbody>")[1].split("</tbody>")[0]


# The visually hidden destination note that rides inside a linked event name.
# It is part of the accessible name and not part of the event's name, so reading
# the column has to drop it — leaving it in makes every linked row unmatchable.
SR_ONLY_NOTE = re.compile(r'<span class="sr-only">.*?</span>', re.S)


def rendered_names(response) -> list[str]:
    """Event names in the order the table rendered them.

    Read from the name cell of each row rather than from a class name, so a
    styling change does not silently make every assertion here vacuous.

    Two details the markup forces. A linked name carries a hidden
    `(koda.ee, avaneb uuel vahelehel)` note inside its anchor, which is dropped
    here; an unlinked one is bare text. And the cell is unescaped, because
    `strip_tags` removes tags without decoding the entities Django wrote for the
    characters inside them.
    """
    names = []
    for row in table_body(response).split("<tr>")[1:]:
        cells = row.split("<td")[1:]
        if len(cells) < NAME_CELL + 1:
            continue
        cell = cells[NAME_CELL].split(">", 1)[1].split("</td>")[0]
        text = strip_tags(SR_ONLY_NOTE.sub("", cell))
        names.append(" ".join(html.unescape(text).split()))
    return names


def rendered_codes(response) -> list[str]:
    """The same rows, named by the service code the fixtures identify them by.

    The service code is no longer a column — the board asked for it off the
    table — so the rows are read by name and translated back here, against the
    published snapshot rather than against a copy of the fixture data. The
    assertions stay written in codes because that is how the fixtures name their
    rows, and a name is the wrong thing to assert on when the point is which
    record was selected.

    Both lookups are assertions rather than fallbacks. A name the snapshot does
    not hold means this helper has stopped reading the column correctly, and
    quietly returning the unmatched string would turn every caller into a
    comparison of names against codes that simply never matches.
    """
    snapshot = get_current_event_programme_snapshot()
    by_name: dict[str, str] = {}
    for name, code in EventProgrammeItem.objects.filter(snapshot=snapshot).values_list(
        "event_name", "service_code"
    ):
        assert name not in by_name, f"two rows share the name {name!r}; read them another way"
        by_name[name] = code

    codes = []
    for name in rendered_names(response):
        assert name in by_name, f"the table rendered {name!r}, which no published row is named"
        codes.append(by_name[name])
    return codes


# -- the programme is the page ------------------------------------------


def test_the_route_and_its_name_are_unchanged(programme):
    assert reverse("events") == PAGE_URL


def test_the_page_leads_with_the_workbook_programme(viewer, programme):
    response = viewer.get(PAGE_URL)

    assert response.status_code == 200
    page = text_of(response)
    assert "Koja sündmuste programm ja ajalugu" in page
    assert "Sündmuste programm" in page
    # The public calendar appears once, named, at the foot.
    assert page.index("Sündmuste programm") < page.index("Koda.ee avalik kalender")


def test_history_is_visible_through_the_year_filter(viewer, programme):
    old_year, _mid_year = programme_years()

    response = viewer.get(PAGE_URL, {"year": old_year})

    assert rendered_codes(response) == ["8002", "8001"]
    assert f"Valitud periood: {old_year}" in text_of(response)


def test_the_default_period_is_the_current_year_when_the_snapshot_has_it(viewer, programme):
    response = viewer.get(PAGE_URL)

    assert f"Valitud periood: {timezone.localdate().year}" in text_of(response)


def test_the_default_period_falls_back_to_the_latest_known_year(viewer, publish_programme):
    """A snapshot with nothing in the current year still opens on real content."""
    _old_year, mid_year = programme_years()
    rows = [
        synthetic_row(
            event_id="EVENT-6001",
            service_code="6001",
            event_name="Sünteetiline vanem sündmus",
            start_date=dt.datetime(mid_year, 5, 5),
            source_row=2,
        )
    ]
    publish_programme(rows=rows, control=default_control(rows))

    response = viewer.get(PAGE_URL)

    assert f"Valitud periood: {mid_year}" in text_of(response)
    assert rendered_codes(response) == ["6001"]


def test_every_year_is_one_explicit_choice(viewer, programme):
    response = viewer.get(PAGE_URL, {"year": "all"})

    assert "Valitud periood: Kõik aastad" in text_of(response)
    assert len(rendered_codes(response)) == PROGRAMME_ROWS


# -- filters ------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ({"year": "all", "tag": "konverents"}, {"8002", "8007"}),
        ({"year": "all", "status": "upcoming"}, {"8005"}),
        ({"year": "all", "public_link": "linked"}, {"8001", "8005"}),
        ({"year": "all", "review": "required"}, {"8003", "8004"}),
        ({"year": "all", "q": "8008"}, {"8008"}),
    ],
)
def test_each_filter_narrows_the_table(viewer, programme, query, expected):
    response = viewer.get(PAGE_URL, query)

    assert set(rendered_codes(response)) == expected


@pytest.mark.parametrize("param", ["event_type", "delivery_mode"])
def test_a_withdrawn_filter_cannot_be_switched_on_from_the_query_string(viewer, programme, param):
    """Tüüp and Toimumisviis are gone from the page, controls and columns alike.

    The selector can still filter on either, and a query string is the one way
    left to ask for it. The page does not ask: a filter that narrows the table
    with nowhere on screen to say it is on would leave a reader looking at a
    short list for no visible reason.
    """
    values = {"event_type": "conference", "delivery_mode": "hybrid"}

    response = viewer.get(PAGE_URL, {"year": "all", param: values[param]})

    assert len(rendered_codes(response)) == PROGRAMME_ROWS


def test_the_table_no_longer_carries_the_type_mode_or_code_columns(viewer, programme):
    response = viewer.get(PAGE_URL, {"year": "all"})
    head = programme_section(response).split("<thead>", 1)[1].split("</thead>", 1)[0]

    for heading in ("Tüüp", "Toimumisviis", "Teenuse kood"):
        assert heading not in head
    for heading in ("Kuupäev", "Sündmus", "Silt", "Seisund"):
        assert heading in head


def test_the_filter_block_no_longer_offers_type_or_delivery_mode(viewer, programme):
    page = body(viewer.get(PAGE_URL))

    assert 'name="event_type"' not in page
    assert 'name="delivery_mode"' not in page
    assert 'name="tag"' in page, "the filters that stayed still render"
    assert 'name="status"' in page


def test_a_service_code_is_still_searchable_without_its_column(viewer, programme):
    """The column went; the code did not stop identifying a row. The search box
    still says it matches a code, so it still has to."""
    response = viewer.get(PAGE_URL, {"year": "all", "q": "8008"})

    assert set(rendered_codes(response)) == {"8008"}


def test_the_page_no_longer_carries_the_export_connection_strip(viewer, programme):
    """The synchronisation state came off this page. What replaced it is
    nothing — the figures and the table still carry the export's own as-of
    date, so no provenance was invented to fill the gap."""
    page = body(viewer.get(PAGE_URL))

    assert "Eksport seisuga" not in page
    assert "Viimane edukas sünkroonimine" not in page


def test_the_figure_strip_no_longer_counts_events_with_a_public_page(viewer, programme):
    """It described the workbook's link column rather than the programme, and
    the Avalik leht filter is where a reader acts on it."""
    page = body(viewer.get(PAGE_URL))
    figures = page.split('id="section-figures"', 1)[1].split("</section>", 1)[0]

    assert "Avaliku lehega" not in figures
    assert "Sündmusi perioodil" in figures
    assert 'name="public_link"' in page, "the filter it duplicated is still offered"


def test_the_month_filter_narrows_the_table(viewer, programme):
    """Membership, because the rows placed relative to today land in whichever
    month the suite happens to run in."""
    response = viewer.get(PAGE_URL, {"year": "all", "month": "02"})

    codes = set(rendered_codes(response))
    assert "8001" in codes, "the February event"
    assert "8002" not in codes, "the May event is not February"


def test_the_quarter_filter_narrows_within_a_year(viewer, programme):
    _old_year, mid_year = programme_years()

    response = viewer.get(PAGE_URL, {"year": mid_year, "quarter": "Q2"})

    assert rendered_codes(response) == ["8009"]


def test_combined_filters_are_reported_together(viewer, programme):
    old_year, _mid_year = programme_years()

    response = viewer.get(
        PAGE_URL, {"year": old_year, "quarter": "Q1", "tag": "seminar", "public_link": "linked"}
    )

    assert rendered_codes(response) == ["8001"]
    assert "Vastavaid sündmusi: 1" in text_of(response)


def test_a_filter_value_the_snapshot_does_not_contain_is_ignored(viewer, programme):
    """An unknown tag must not empty the table without explanation, and must not
    reach the database as an arbitrary value."""
    response = viewer.get(PAGE_URL, {"year": "all", "tag": "puudub-sellist-silti"})

    assert len(rendered_codes(response)) == PROGRAMME_ROWS


def test_clearing_the_filters_returns_to_the_default_period(viewer, programme):
    filtered = viewer.get(PAGE_URL, {"year": "all", "tag": "seminar"})

    assert f'href="{PAGE_URL}"' in body(filtered)

    cleared = viewer.get(PAGE_URL)
    assert f"Valitud periood: {timezone.localdate().year}" in text_of(cleared)


def test_filtering_needs_no_javascript(viewer, programme):
    """A plain GET form with real controls, submitted by a real button."""
    page = body(viewer.get(PAGE_URL))

    assert '<form method="get"' in page
    assert '<select id="filter-year" name="year"' in page
    assert '<button type="submit"' in page


# -- unknown dates ------------------------------------------------------


def test_an_undated_event_says_so_and_is_disclosed(viewer, programme):
    default_view = viewer.get(PAGE_URL)

    assert "Kuupäev teadmata: 1" in text_of(default_view), "disclosed even when filtered out"

    reachable = viewer.get(PAGE_URL, {"year": "all", "status": "date_unknown"})
    assert rendered_codes(reachable) == ["8004"]
    assert "Kuupäev teadmata" in strip_tags(table_body(reachable))


def test_an_undated_event_never_borrows_its_source_year_as_a_date(viewer, programme):
    response = viewer.get(PAGE_URL, {"year": "all", "status": "date_unknown"})

    # The synthetic undated row sits on the 2099 annual sheet. That is not a date
    # and must not appear as one.
    assert "2099" not in strip_tags(table_body(response))


def test_the_review_count_is_disclosed_and_reachable(viewer, programme):
    default_view = viewer.get(PAGE_URL)

    assert "Vajab ülevaatust: 2" in text_of(default_view)

    reachable = viewer.get(PAGE_URL, {"year": "all", "review": "required"})
    assert set(rendered_codes(reachable)) == {"8003", "8004"}


# -- public links -------------------------------------------------------


def test_a_linked_event_links_its_name_with_the_project_wording(viewer, programme):
    rows = table_body(viewer.get(PAGE_URL, {"year": "all", "public_link": "linked"}))

    assert f'href="{SYNTHETIC_URL}"' in rows
    assert "(koda.ee, avaneb uuel vahelehel)" in rows
    assert 'rel="noopener noreferrer"' in rows


def test_an_unlinked_event_renders_its_name_as_plain_text(viewer, programme):
    rows = table_body(viewer.get(PAGE_URL, {"year": "all", "public_link": "unlinked"}))

    assert "Sünteetiline mai konverents" in strip_tags(rows)
    assert "<a " not in rows, "no anchor in a row the workbook did not link"
    assert "(koda.ee, avaneb uuel vahelehel)" not in rows


def test_no_link_is_inferred_from_a_matching_title_or_date(viewer, programme):
    """The public calendar carrying the same title on the same day changes nothing.

    Linking is a decision the workbook makes in `DASH_URL_OVERRIDES`. A title
    match, a normalised-title match, a title-plus-date match and a service-code
    guess are all forbidden, so an unlinked programme row stays unlinked however
    closely a public event resembles it.
    """
    from apps.core.canonical import canonical_checksum
    from apps.events.collector import EventCollection, EventEntry
    from apps.events.sync import synchronize_events

    old_year, _mid_year = programme_years()
    unlinked_name = "Sünteetiline mai konverents"
    public_url = "https://www.koda.ee/et/sundmused/sunteetiline-mai-konverents"
    entry = EventEntry(
        stable_key="public-same-title",
        title=unlinked_name,
        canonical_url=public_url,
        category="",
        summary="",
        starts_on=dt.date(old_year, 5, 20),
        ends_on=None,
        starts_at=None,
        ends_at=None,
        location="",
        source_order=0,
    )
    canonical = {"dataset": "koda-public-events", "schema_version": "1.0", "items": ["synthetic"]}
    checksum, size = canonical_checksum(canonical)
    synchronize_events(
        collector=lambda **_kwargs: EventCollection(
            entries=(entry,),
            sha256=checksum,
            size_bytes=size,
            canonical=canonical,
            pages_fetched=1,
            details_fetched=1,
            skipped_non_events=0,
            skipped_past=0,
        )
    )

    response = viewer.get(PAGE_URL, {"year": "all", "q": "konverents"})
    rows = table_body(response)

    assert unlinked_name in strip_tags(rows)
    assert public_url not in body(response)


# -- pagination ---------------------------------------------------------


@pytest.fixture
def long_programme(publish_programme):
    """More rows in one year than a page holds, plus a handful in another year."""
    old_year, mid_year = programme_years()
    rows = [
        synthetic_row(
            event_id=f"EVENT-5{index:03d}",
            service_code=f"5{index:03d}",
            event_name=f"Sünteetiline sündmus {index:03d}",
            # One per day from the start of the year, so ordering is unambiguous.
            start_date=dt.datetime(mid_year, 1, 1) + dt.timedelta(days=index),
            source_year=mid_year,
            source_sheet=f"KOOD {mid_year}",
            source_row=index + 2,
        )
        for index in range(PAGE_SIZE + 12)
    ]
    rows += [
        synthetic_row(
            event_id=f"EVENT-3{index:03d}",
            service_code=f"3{index:03d}",
            event_name=f"Sünteetiline varasem sündmus {index:03d}",
            start_date=dt.datetime(old_year, 6, 1) + dt.timedelta(days=index),
            source_year=old_year,
            source_sheet=f"KOOD {old_year}",
            source_row=index + 2,
        )
        for index in range(5)
    ]
    publish_programme(rows=rows, control=default_control(rows))
    return get_current_event_programme_snapshot()


def test_the_first_page_holds_the_default_page_size(viewer, long_programme):
    _old_year, mid_year = programme_years()

    response = viewer.get(PAGE_URL, {"year": mid_year})

    assert len(rendered_codes(response)) == PAGE_SIZE
    assert f"Vastavaid sündmusi: {PAGE_SIZE + 12}" in text_of(response)
    assert "Lehekülg 1 / 2" in text_of(response)


def test_the_second_page_holds_the_remainder_and_repeats_no_row(viewer, long_programme):
    _old_year, mid_year = programme_years()

    first = rendered_codes(viewer.get(PAGE_URL, {"year": mid_year}))
    second_response = viewer.get(PAGE_URL, {"year": mid_year, "page": 2})
    second = rendered_codes(second_response)

    assert len(second) == 12
    assert not set(first) & set(second)
    assert "Lehekülg 2 / 2" in text_of(second_response)


def test_the_second_page_still_honours_the_year_filter(viewer, long_programme):
    """The five rows from the other year must not leak onto the last page."""
    _old_year, mid_year = programme_years()

    second = rendered_codes(viewer.get(PAGE_URL, {"year": mid_year, "page": 2}))

    assert all(code.startswith("5") for code in second), second


def test_a_pagination_link_carries_every_active_filter(viewer, long_programme):
    _old_year, mid_year = programme_years()

    # Both filters keep the whole year's rows, so page two still exists to link
    # to. A filter narrow enough to fit on one page would make this vacuous.
    response = viewer.get(PAGE_URL, {"year": mid_year, "q": "Sünteetiline"})
    page = body(response)

    assert f"year={mid_year}" in page
    assert "q=S%C3%BCnteetiline" in page
    assert "page=2" in page


def test_an_out_of_range_page_still_renders(viewer, long_programme):
    _old_year, mid_year = programme_years()

    response = viewer.get(PAGE_URL, {"year": mid_year, "page": 999})

    assert response.status_code == 200
    assert rendered_codes(response)


def test_the_query_count_does_not_grow_with_the_number_of_rows(viewer, publish_programme):
    """An N+1 regression guard that does not pin an exact query count.

    Rendering 48 rows must cost the same number of queries as rendering 5. The
    absolute number is an implementation detail that would move whenever
    unrelated page content changed; the invariant is that it does not depend on
    how many rows are on the page.
    """
    _old_year, mid_year = programme_years()

    def rows_for(count: int) -> list[dict]:
        return [
            synthetic_row(
                event_id=f"EVENT-4{index:03d}",
                service_code=f"4{index:03d}",
                event_name=f"Sünteetiline sündmus {index:03d}",
                start_date=dt.datetime(mid_year, 1, 1) + dt.timedelta(days=index),
                source_year=mid_year,
                source_sheet=f"KOOD {mid_year}",
                source_row=index + 2,
            )
            for index in range(count)
        ]

    small = rows_for(5)
    publish_programme(rows=small, control=default_control(small))
    with CaptureQueriesContext(connection) as few_rows:
        viewer.get(PAGE_URL, {"year": mid_year})

    large = rows_for(48)
    publish_programme(rows=large, control=default_control(large))
    with CaptureQueriesContext(connection) as many_rows:
        viewer.get(PAGE_URL, {"year": mid_year})

    assert len(many_rows) == len(few_rows)


# -- what the page will not do -----------------------------------------


@pytest.mark.parametrize(
    "forbidden",
    # Whole words rather than the bare stem "hind", which is a substring of the
    # English "behind" and would fail on an ordinary code comment one day.
    ["liikmehind", "hinnad", "€", "osaleja", "osalejaid", "registreeri", "kohad täis", "soodus"],
)
def test_no_price_or_participant_information_reaches_the_page(viewer, programme, forbidden):
    assert forbidden not in text_of(viewer.get(PAGE_URL)).casefold()


def test_no_price_or_participant_field_exists_on_the_model():
    """The columns are parsed past, never parsed in.

    A field that does not exist cannot leak, cannot be added to a template by
    accident and needs no migration to remove.
    """
    from apps.event_programme.models import EventProgrammeItem

    names = {field.name for field in EventProgrammeItem._meta.get_fields()}
    for forbidden in (
        "member_price_eur",
        "nonmember_price_eur",
        "later_member_price_eur",
        "later_nonmember_price_eur",
        "price_status",
        "discount_code",
        "participant_count",
        "registration_count",
        "attendee_count",
    ):
        assert forbidden not in names, forbidden
    assert not any("price" in name or "participant" in name for name in names)


def test_the_page_shows_no_internal_row_number_or_warning_code(viewer, programme):
    page = programme_section(viewer.get(PAGE_URL, {"year": "all"}))

    assert "date_unparsed" not in page
    assert "Lähterida" not in strip_tags(page)


def test_the_page_carries_no_inline_style(viewer, programme):
    """The Content Security Policy is `style-src 'self'`.

    Every filter control and every badge is styled by the bundled stylesheet, so a
    proportion or a colour may never arrive as a `style` attribute.
    """
    assert 'style="' not in body(viewer.get(PAGE_URL, {"year": "all"}))


def test_rendering_the_page_opens_no_socket(viewer, programme, monkeypatch):
    """A page render reads PostgreSQL and nothing else.

    The workbook is downloaded only by `sync_event_programme`; no request may
    contact OneDrive, koda.ee or any other remote system.
    """
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("rendering /sundmused/ must not open a socket")

    monkeypatch.setattr(socket, "create_connection", refuse)

    response = viewer.get(PAGE_URL, {"year": "all"})

    assert response.status_code == 200


def test_the_page_reports_nothing_when_no_programme_is_published(viewer):
    response = viewer.get(PAGE_URL)

    assert response.status_code == 200
    page = text_of(response)
    assert "Andmeallikas ei ole veel ühendatud." in page
    assert "Vastavaid sündmusi" not in page
