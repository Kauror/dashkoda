"""Searching the whole legal-work register, not just the two standing lists.

`Hetkel töös` draws the open records and `Viimati välja läinud` the fifteen
newest sends — 33 of the 612 in the production snapshot. Every other record was
imported, matched to its public consultation, and then unreachable from the page
that is about it. The first test states exactly that.

Every record here arrives through the real workbook importer, so a search is
answering questions about rows the application actually publishes.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse

from apps.legal_work.importer import import_artifact
from apps.legal_work.search import PER_PAGE, build_search, parse_query, parse_status
from apps.legal_work.selectors import (
    DEFAULT_RECENT_LIMIT,
    SEARCH_ALL,
    SEARCH_OPEN,
    SEARCH_SENT,
    get_latest_sent_items,
    get_open_items,
)

from .workbook_factory import synthetic_row

pytestmark = pytest.mark.django_db


def publish(rows, make_workbook, register_workbook):
    """Publish a snapshot built from `rows` and return it."""
    artifact = register_workbook(make_workbook(rows=rows))
    return import_artifact(artifact, dry_run=False).snapshot


def topics(section) -> list[str]:
    return [row.topic for row in section.results]


# -- the regression this exists for ------------------------------------------


def test_a_concluded_record_is_findable_though_no_list_shows_it(make_workbook, register_workbook):
    """The key test.

    One open topic, one concluded topic, and enough newer sends that the
    concluded one is off the end of `Viimati välja läinud`. It appears in
    neither standing list and must be findable anyway.
    """
    rows = [
        synthetic_row(record_id="SYN-OPEN", topic="Avatud teema", source_row=2),
        synthetic_row(
            record_id="SYN-OLD",
            topic="Ammune lõpetatud teema",
            sent_date=dt.date(2099, 1, 1),
            sent_status="sent",
            is_open=False,
            source_row=3,
        ),
    ]
    for index in range(DEFAULT_RECENT_LIMIT + 2):
        rows.append(
            synthetic_row(
                record_id=f"SYN-NEW-{index:02d}",
                topic=f"Uuem saadetud teema {index}",
                sent_date=dt.date(2099, 6, 1) + dt.timedelta(days=index),
                sent_status="sent",
                is_open=False,
                source_row=4 + index,
            )
        )
    snapshot = publish(rows, make_workbook, register_workbook)

    standing = {item.topic for item in get_open_items(snapshot)} | {
        item.topic for item in get_latest_sent_items(snapshot, limit=DEFAULT_RECENT_LIMIT)
    }
    assert "Ammune lõpetatud teema" not in standing, "the fixture no longer proves anything"

    found = build_search(snapshot, query="Ammune")

    assert topics(found) == ["Ammune lõpetatud teema"]


# -- what it searches --------------------------------------------------------


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("Otsitav", True),  # topic
        ("SYN-FIND", True),  # record id
        ("Erakordne liik", True),  # act type
        ("Sünteetiline ministeerium", True),  # recipient
        ("kooskõlastusel", True),  # stage
        ("saata vastus", True),  # next step
        ("ei-esine-kusagil", False),
    ],
)
def test_every_field_a_reader_recognises_is_searchable(
    make_workbook, register_workbook, term, expected
):
    snapshot = publish(
        [
            synthetic_row(
                record_id="SYN-FIND",
                topic="Otsitav teema",
                act_type="Erakordne liik",
                recipient="Sünteetiline ministeerium",
                stage="kooskõlastusel",
                next_step="saata vastus",
                source_row=2,
            )
        ],
        make_workbook,
        register_workbook,
    )

    found = build_search(snapshot, query=term)

    assert bool(topics(found)) is expected


def test_the_search_is_case_insensitive(make_workbook, register_workbook):
    snapshot = publish(
        [synthetic_row(record_id="SYN-1", topic="Käibemaksuseaduse muudatus", source_row=2)],
        make_workbook,
        register_workbook,
    )

    assert topics(build_search(snapshot, query="KÄIBEMAKSU"))
    assert topics(build_search(snapshot, query="käibemaksu"))


def test_no_term_searches_nothing_and_leaves_the_page_as_it_was(make_workbook, register_workbook):
    """The section is a mode. Without a term it renders its box and no rows,
    so a reader who never searches sees the page unchanged."""
    snapshot = publish(
        [synthetic_row(record_id="SYN-1", topic="Teema", source_row=2)],
        make_workbook,
        register_workbook,
    )

    section = build_search(snapshot, query="")

    assert not section.is_searching
    assert section.results == ()
    assert section.total == 0


def test_a_retired_snapshot_never_answers(make_workbook, register_workbook):
    """Searching yesterday's revision would present a stage that has since
    changed as though it were today's position."""
    publish(
        [synthetic_row(record_id="SYN-1", topic="Vana redaktsioon", source_row=2)],
        make_workbook,
        register_workbook,
    )
    current = publish(
        [synthetic_row(record_id="SYN-2", topic="Uus redaktsioon", source_row=2)],
        make_workbook,
        register_workbook,
    )

    # Resolved from the current snapshot, exactly as the view does.
    assert topics(build_search(current, query="redaktsioon")) == ["Uus redaktsioon"]


# -- the status filter -------------------------------------------------------


def test_the_status_chips_narrow_without_losing_the_term(make_workbook, register_workbook):
    snapshot = publish(
        [
            synthetic_row(record_id="SYN-O", topic="Maksuteema avatud", source_row=2),
            synthetic_row(
                record_id="SYN-S",
                topic="Maksuteema saadetud",
                sent_date=dt.date(2099, 2, 2),
                sent_status="sent",
                is_open=False,
                source_row=3,
            ),
        ],
        make_workbook,
        register_workbook,
    )

    assert len(topics(build_search(snapshot, query="Maksuteema"))) == 2
    assert topics(build_search(snapshot, query="Maksuteema", status=SEARCH_OPEN)) == [
        "Maksuteema avatud"
    ]
    assert topics(build_search(snapshot, query="Maksuteema", status=SEARCH_SENT)) == [
        "Maksuteema saadetud"
    ]


def test_a_not_sent_record_is_not_a_send(make_workbook, register_workbook):
    """Explicitly not sent is a decision, not a dispatch — the same rule
    `get_latest_sent_items` follows."""
    snapshot = publish(
        [
            synthetic_row(
                record_id="SYN-N",
                topic="Saatmata jäetud teema",
                sent_status="not_sent",
                sent_date=None,
                is_open=False,
                source_row=2,
            )
        ],
        make_workbook,
        register_workbook,
    )

    assert topics(build_search(snapshot, query="Saatmata")) == ["Saatmata jäetud teema"]
    assert topics(build_search(snapshot, query="Saatmata", status=SEARCH_SENT)) == []


def test_every_chip_carries_the_term():
    section = build_search(None, query="käibemaks", status=SEARCH_OPEN)

    for option in section.statuses:
        assert "otsing=k%C3%A4ibemaks" in option.query


# -- pagination --------------------------------------------------------------


def test_results_paginate_and_keep_the_whole_query(make_workbook, register_workbook):
    rows = [
        synthetic_row(
            record_id=f"SYN-{index:03d}", topic=f"Korduv teema {index}", source_row=2 + index
        )
        for index in range(PER_PAGE + 3)
    ]
    snapshot = publish(rows, make_workbook, register_workbook)

    first = build_search(snapshot, query="Korduv", status=SEARCH_OPEN)

    assert first.total == PER_PAGE + 3
    assert len(first.results) == PER_PAGE
    assert first.total_pages == 2
    assert "otsing=Korduv" in first.next_query
    assert "seis=toos" in first.next_query
    assert "lk=2" in first.next_query

    second = build_search(snapshot, query="Korduv", status=SEARCH_OPEN, page=2)
    assert len(second.results) == 3
    assert not set(topics(first)) & set(topics(second))


def test_a_page_past_the_end_falls_back(make_workbook, register_workbook):
    snapshot = publish(
        [synthetic_row(record_id="SYN-1", topic="Ainus teema", source_row=2)],
        make_workbook,
        register_workbook,
    )

    section = build_search(snapshot, query="Ainus", page=99)

    assert section.page_number == 1
    assert section.has_results


# -- input handling ----------------------------------------------------------


@pytest.mark.parametrize(
    "raw", ["x" * 5000, "'; drop table--", "%%%", "../../etc/passwd", "üõäö", None, ""]
)
def test_a_hostile_or_absent_term_is_bounded_and_harmless(raw):
    assert len(parse_query(raw)) <= 120


@pytest.mark.parametrize("raw", ["väljamõeldud", "", None, "toos"])
def test_an_unreadable_status_falls_back_to_all(raw):
    assert parse_status(raw) in (SEARCH_ALL, SEARCH_OPEN)


@pytest.mark.parametrize(
    "query",
    [
        {"otsing": "x" * 5000},
        {"otsing": "teema", "seis": "väljamõeldud"},
        {"otsing": "teema", "lk": "-4"},
        {"otsing": "teema", "lk": "banana"},
        {"seis": "toos"},
    ],
)
def test_a_malformed_query_renders_a_page_rather_than_an_error(viewer, query):
    response = viewer.get(reverse("legal-work"), query)

    assert response.status_code == 200


# -- through the page --------------------------------------------------------


def test_the_page_searches_and_says_what_it_searched(viewer, make_workbook, register_workbook):
    publish(
        [
            synthetic_row(record_id="SYN-1", topic="Leitav teema", source_row=2),
            synthetic_row(record_id="SYN-2", topic="Muu teema", source_row=3),
        ],
        make_workbook,
        register_workbook,
    )

    body = viewer.get(reverse("legal-work"), {"otsing": "Leitav"}).content.decode()

    assert "Otsi registrist" in body
    assert "Leitav teema" in body
    assert "1 kirje." in body


def test_the_standing_lists_stay_while_searching(viewer, make_workbook, register_workbook):
    """The search is in addition to them, not instead of them."""
    publish(
        [synthetic_row(record_id="SYN-1", topic="Avatud teema", source_row=2)],
        make_workbook,
        register_workbook,
    )

    body = viewer.get(reverse("legal-work"), {"otsing": "Avatud"}).content.decode()

    assert "Hetkel töös" in body
    assert "Viimati välja läinud" in body


def test_the_page_states_the_population_before_a_search(viewer, make_workbook, register_workbook):
    """A result list cannot tell a reader that the search covered everything."""
    publish(
        [synthetic_row(record_id="SYN-1", topic="Teema", source_row=2)],
        make_workbook,
        register_workbook,
    )

    body = viewer.get(reverse("legal-work")).content.decode()

    assert "Otsitakse kõigist registri kirjetest" in body


def test_the_search_costs_no_extra_link_query(
    viewer, make_workbook, register_workbook, django_assert_max_num_queries
):
    """The whole page resolves public links once. A search that resolved its own
    would let a record link one way in a result and another way in a list, and
    nothing on the page would look wrong."""
    publish(
        [synthetic_row(record_id="SYN-1", topic="Avatud teema", source_row=2)],
        make_workbook,
        register_workbook,
    )

    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as plain:
        viewer.get(reverse("legal-work"))
    with CaptureQueriesContext(connection) as searched:
        viewer.get(reverse("legal-work"), {"otsing": "Avatud"})

    # A count and a page slice, and nothing else — in particular no second link
    # resolution, which would be one query per extra list.
    assert len(searched) - len(plain) <= 2


# -- the live-search fragment ------------------------------------------------
#
# The same contract the Nähtavus searches follow: the form submits and reloads
# without JavaScript, and with the bundle the identical partial is swapped in on
# each keystroke. Both paths render one template, which is the only thing that
# keeps them from drifting.

FRAGMENT = "legal-work-search"


def test_the_fragment_is_a_fragment(viewer, make_workbook, register_workbook):
    publish(
        [synthetic_row(record_id="SYN-1", topic="Leitav teema", source_row=2)],
        make_workbook,
        register_workbook,
    )

    content = viewer.get(reverse(FRAGMENT), {"otsing": "Leitav"}).content.decode()

    assert "Leitav teema" in content
    for shell in ("<html", "<body", "Peamenüü", "Koja töölaud", "Hetkel töös"):
        assert shell not in content
    # The box must never come back in the swap: htmx replaces this region's
    # contents, and an input inside it loses the caret on every keystroke.
    assert 'type="search"' not in content
    assert "<form" not in content


def test_the_fragment_and_the_page_render_the_same_rows(viewer, make_workbook, register_workbook):
    """One template, two paths. If these ever disagree, a reader typing sees
    something a reader reloading does not."""
    publish(
        [
            synthetic_row(record_id="SYN-1", topic="Käibemaksu teema", source_row=2),
            synthetic_row(record_id="SYN-2", topic="Muu teema", source_row=3),
        ],
        make_workbook,
        register_workbook,
    )

    fragment = viewer.get(reverse(FRAGMENT), {"otsing": "Käibemaksu"}).content.decode()
    page = viewer.get(reverse("legal-work"), {"otsing": "Käibemaksu"}).content.decode()

    assert "Käibemaksu teema" in fragment
    assert "Käibemaksu teema" in page
    assert "Muu teema" not in fragment
    assert "1 kirje." in fragment
    assert "1 kirje." in page


def test_the_fragment_keeps_the_status_it_was_given(viewer, make_workbook, register_workbook):
    publish(
        [
            synthetic_row(record_id="SYN-O", topic="Maksuteema avatud", source_row=2),
            synthetic_row(
                record_id="SYN-S",
                topic="Maksuteema saadetud",
                sent_date=dt.date(2099, 2, 2),
                sent_status="sent",
                is_open=False,
                source_row=3,
            ),
        ],
        make_workbook,
        register_workbook,
    )

    narrowed = viewer.get(
        reverse(FRAGMENT), {"otsing": "Maksuteema", "seis": SEARCH_OPEN}
    ).content.decode()

    assert "Maksuteema avatud" in narrowed
    assert "Maksuteema saadetud" not in narrowed


def test_the_fragment_pushes_the_page_it_belongs_to(viewer):
    response = viewer.get(reverse(FRAGMENT), {"otsing": "käibemaks"})

    pushed = response.headers["HX-Push-Url"]
    assert pushed.startswith(reverse("legal-work"))
    assert "otsing=k%C3%A4ibemaks" in pushed
    assert pushed.endswith("#section-search")


def test_the_fragment_resets_the_page_number(viewer):
    """A reader on page 3 who types a new term is asking a new question, and
    carrying `lk` into it answers "no results" for anything shorter."""
    response = viewer.get(reverse(FRAGMENT), {"otsing": "uus", "lk": "3"})

    assert "lk=" not in response.headers["HX-Push-Url"]


def test_the_fragment_carries_the_status_into_the_address_bar(viewer):
    response = viewer.get(
        reverse(FRAGMENT),
        {"otsing": "x", "seis": SEARCH_OPEN},
        headers={"HX-Current-URL": "https://dash.orgusaar.ee/oigusloome/?seis=toos"},
    )

    assert "seis=toos" in response.headers["HX-Push-Url"]


def test_a_cleared_box_pushes_the_unfiltered_page(viewer):
    """An empty term removes its key rather than pushing `?otsing=`."""
    response = viewer.get(reverse(FRAGMENT), {"otsing": ""})

    assert "otsing=" not in response.headers["HX-Push-Url"]


def test_the_fragment_still_offers_the_status_chips(viewer, make_workbook, register_workbook):
    """They live inside the swapped region precisely so each one carries the
    term the reader has just typed; outside, they would still hold whatever was
    there at page load."""
    publish(
        [synthetic_row(record_id="SYN-1", topic="Käibemaksu teema", source_row=2)],
        make_workbook,
        register_workbook,
    )

    content = viewer.get(reverse(FRAGMENT), {"otsing": "Käibemaksu"}).content.decode()

    assert "Välja läinud" in content
    assert "otsing=K%C3%A4ibemaksu" in content


def test_the_fragment_is_behind_the_viewer_gate(client):
    response = client.get(reverse(FRAGMENT), {"otsing": "x"})

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/sisene/")
