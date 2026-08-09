"""The GA4 collector: what it asks for, and every shape it can be answered with.

No database and no credential. `Ga4ApiCollector` takes its session, so every
response GA4 can produce — including the ones that used to raise — is reachable
here without a network.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.visibility.ga4 import (
    PAGE_SIZE,
    Ga4ApiCollector,
    Ga4Configuration,
    Ga4NotConfigured,
    Ga4ResponseError,
)

DAY = dt.date(2026, 8, 8)
NEXT = dt.date(2026, 8, 9)

#: Deliberately not the Chamber's own property. A property ID is not a
#: credential, but it is operational detail and a public test suite is a poor
#: place to keep it. The assertions below are about whether an error message
#: echoes the *configured* ID, which any value proves just as well.
CONFIGURED = Ga4Configuration(property_id="123456789", credentials_file="/run/secrets/ga4.json")


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    """Answers each `runReport` in the order the collector asks its reports."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []
        self.slept = []

    def post(self, url, json=None, timeout=None):
        self.requests.append(json)
        if not self._responses:
            raise AssertionError("the collector asked for more reports than were prepared")
        answer = self._responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def rows(*items):
    return {"rowCount": len(items), "rows": list(items)}


def site_row(day: dt.date, *values):
    return {
        "dimensionValues": [{"value": day.strftime("%Y%m%d")}],
        "metricValues": [{"value": str(v) if v is not None else ""} for v in values],
    }


def page_row(day: dt.date, path: str, *values):
    return {
        "dimensionValues": [{"value": day.strftime("%Y%m%d")}, {"value": path}],
        "metricValues": [{"value": str(v) if v is not None else ""} for v in values],
    }


def channel_row(day: dt.date, channel: str, *values):
    return {
        "dimensionValues": [{"value": day.strftime("%Y%m%d")}, {"value": channel}],
        "metricValues": [{"value": str(v) if v is not None else ""} for v in values],
    }


def collector(*responses, sleep=None):
    """A collector whose session answers with `responses`, in order.

    A bare dict is the ordinary 200; a `FakeResponse` is passed through so a
    test can choose the status code, and an exception is raised at the transport
    layer. Re-wrapping a `FakeResponse` would bury its status inside a 200 and
    quietly turn every retry test into a success.
    """
    prepared = [
        item if isinstance(item, FakeResponse | Exception) else FakeResponse(item)
        for item in responses
    ]
    session = FakeSession(prepared)
    made = Ga4ApiCollector(CONFIGURED, session=session, sleep=sleep or (lambda _: None))
    return made, session


# -- configuration -------------------------------------------------------


def test_an_unconfigured_collector_refuses_to_be_built():
    with pytest.raises(Ga4NotConfigured) as error:
        Ga4ApiCollector(Ga4Configuration(property_id="", credentials_file=""))

    assert "GA4_PROPERTY_ID" in str(error.value)
    assert "GA4_CREDENTIALS_FILE" in str(error.value)


def test_the_error_never_echoes_the_credential_path():
    with pytest.raises(Ga4NotConfigured) as error:
        Ga4ApiCollector(Ga4Configuration(property_id="123456789", credentials_file=""))

    assert "/run/secrets" not in str(error.value)


# -- the ordinary answer -------------------------------------------------


def test_one_request_per_report_covers_a_whole_range():
    """The reason this is a range collector: a day per request would be 1 800
    requests for five years."""
    made, session = collector(
        rows(site_row(DAY, 100, 80, 30, 200, 60, 5000), site_row(NEXT, 110, 85, 32, 220, 66, 5200)),
        rows(
            page_row(DAY, "/et/uudised/a", 50, 40, 900),
            page_row(NEXT, "/et/uudised/a", 60, 45, 950),
        ),
        rows(channel_row(DAY, "Organic Search", 70, 40), channel_row(NEXT, "Direct", 30, 12)),
    )

    collection = made.collect_range(start=DAY, end=NEXT)

    assert len(session.requests) == 3
    assert sorted(collection.days) == [DAY, NEXT]
    assert collection.counts.requests == 3


def test_every_site_metric_lands_on_the_field_named_for_it():
    made, _ = collector(rows(site_row(DAY, 100, 80, 30, 200, 60, 5000)), rows(), rows())

    day = made.collect_range(start=DAY, end=DAY).days[DAY]

    assert day.sessions == 100
    assert day.active_users == 80
    assert day.new_users == 30
    assert day.page_views == 200
    assert day.engaged_sessions == 60
    assert day.user_engagement_seconds == 5000


def test_page_rows_are_canonicalised_and_keep_the_raw_path_when_it_differed():
    made, _ = collector(
        rows(site_row(DAY, 1, 1, 1, 1, 1, 1)),
        rows(page_row(DAY, "/et/uudised/a/?utm_source=x", 50, 40, 900)),
        rows(),
    )

    page = made.collect_range(start=DAY, end=DAY).days[DAY].pages[0]

    assert page.path == "/et/uudised/a"
    assert page.raw_path == "/et/uudised/a/?utm_source=x"


def test_rows_that_canonicalise_together_are_folded_into_one_page():
    """GA4 reports `/x`, `/x/` and `/x?utm=…` separately. They are one article,
    and three rows would violate the per-day uniqueness of a path."""
    made, _ = collector(
        rows(site_row(DAY, 1, 1, 1, 1, 1, 1)),
        rows(
            page_row(DAY, "/et/uudised/a", 50, 40, 900),
            page_row(DAY, "/et/uudised/a/", 20, 15, 300),
            page_row(DAY, "/et/uudised/a?utm_source=fb", 30, 25, 600),
        ),
        rows(),
    )

    pages = made.collect_range(start=DAY, end=DAY).days[DAY].pages

    assert len(pages) == 1
    assert pages[0].page_views == 100
    assert pages[0].user_engagement_seconds == 1800


def test_folding_never_adds_up_the_people():
    """Views add; readers do not. The same person can arrive by two spellings of
    one URL, so the folded row keeps the largest daily count rather than a sum
    that would exceed the article's actual audience."""
    made, _ = collector(
        rows(site_row(DAY, 1, 1, 1, 1, 1, 1)),
        rows(
            page_row(DAY, "/et/uudised/a", 50, 40, 900),
            page_row(DAY, "/et/uudised/a/", 20, 15, 300),
        ),
        rows(),
    )

    page = made.collect_range(start=DAY, end=DAY).days[DAY].pages[0]

    assert page.active_users == 40
    assert page.active_users != 55


def test_a_path_that_cannot_be_named_is_dropped_rather_than_filed_under_a_placeholder():
    made, _ = collector(
        rows(site_row(DAY, 1, 1, 1, 1, 1, 1)),
        rows(page_row(DAY, "", 50, 40, 900), page_row(DAY, "/et/uudised/a", 10, 8, 100)),
        rows(),
    )

    pages = made.collect_range(start=DAY, end=DAY).days[DAY].pages

    assert [page.path for page in pages] == ["/et/uudised/a"]


# -- the empty answers ---------------------------------------------------


def test_a_day_with_no_rows_is_an_absence_of_measurement_and_not_a_zero():
    """The defect that killed the first collector on its first quiet day, and the
    rule the whole dashboard rests on."""
    made, _ = collector(rows(), rows(), rows())

    day = made.collect_range(start=DAY, end=DAY).days[DAY]

    assert day.sessions is None
    assert day.page_views is None
    assert day.has_any_figure is False
    assert day.pages == ()


def test_a_range_gets_a_reading_for_every_day_even_the_silent_ones():
    """A gap in GA4's rows is still a day, and a day with no revision at all is
    indistinguishable from one never collected."""
    made, _ = collector(rows(site_row(NEXT, 5, 4, 1, 9, 3, 60)), rows(), rows())

    days = made.collect_range(start=DAY, end=NEXT).days

    assert sorted(days) == [DAY, NEXT]
    assert days[DAY].has_any_figure is False
    assert days[NEXT].sessions == 5


def test_asking_for_neither_pages_nor_channels_makes_one_request():
    made, session = collector(rows(site_row(DAY, 1, 1, 1, 1, 1, 1)))

    day = made.collect_range(start=DAY, end=DAY, with_pages=False, with_channels=False).days[DAY]

    assert len(session.requests) == 1
    assert day.has_page_detail is False
    assert day.has_channel_detail is False


# -- pagination ----------------------------------------------------------


def test_pagination_follows_the_row_count_to_the_end():
    first = {"rowCount": 3, "rows": [page_row(DAY, "/a", 1, 1, 1), page_row(DAY, "/b", 2, 1, 1)]}
    second = {"rowCount": 3, "rows": [page_row(DAY, "/c", 3, 1, 1)]}
    made, session = collector(rows(site_row(DAY, 1, 1, 1, 1, 1, 1)), first, second, rows())

    pages = made.collect_range(start=DAY, end=DAY).days[DAY].pages

    assert [page.path for page in pages] == ["/a", "/b", "/c"]
    assert session.requests[1]["offset"] == 0
    assert session.requests[2]["offset"] == 2
    assert session.requests[1]["limit"] == PAGE_SIZE


def test_pagination_stops_when_a_page_comes_back_empty():
    """A response claiming more rows than it ever returns must not spin against
    a live quota."""
    lying = {"rowCount": 99, "rows": [page_row(DAY, "/a", 1, 1, 1)]}
    empty = {"rowCount": 99, "rows": []}
    made, session = collector(rows(site_row(DAY, 1, 1, 1, 1, 1, 1)), lying, empty, rows())

    made.collect_range(start=DAY, end=DAY)

    assert len(session.requests) == 4


# -- responses this application refuses to read --------------------------


@pytest.mark.parametrize(
    "payload",
    [
        "not a document",
        {"rows": "not a list"},
        {"rows": [{"dimensionValues": [{"value": "20260808"}]}]},
        {"rows": [{"dimensionValues": [{"value": "20260808"}], "metricValues": [{}]}]},
        {"rows": [{"dimensionValues": [{"value": "not-a-date"}], "metricValues": []}]},
    ],
)
def test_a_malformed_response_raises_our_own_error(payload):
    made, _ = collector(payload, rows(), rows())

    with pytest.raises(Ga4ResponseError):
        made.collect_range(start=DAY, end=DAY)


def test_a_value_that_is_not_a_number_is_refused():
    made, _ = collector(rows(site_row(DAY, "many", 1, 1, 1, 1, 1)), rows(), rows())

    with pytest.raises(Ga4ResponseError) as error:
        made.collect_range(start=DAY, end=DAY)

    assert "arv" in str(error.value)


def test_a_fractional_duration_is_read_rather_than_refused():
    """`userEngagementDuration` arrives as `286086.0`, and refusing it would
    fail every ordinary day."""
    made, _ = collector(rows(site_row(DAY, 1, 1, 1, 1, 1, "286086.0")), rows(), rows())

    day = made.collect_range(start=DAY, end=DAY).days[DAY]

    assert day.user_engagement_seconds == 286086


def test_the_wrong_number_of_metrics_is_refused():
    made, _ = collector(rows(site_row(DAY, 1, 2)), rows(), rows())

    with pytest.raises(Ga4ResponseError):
        made.collect_range(start=DAY, end=DAY)


# -- failure and retry ---------------------------------------------------


def test_a_rate_limit_is_retried_and_then_succeeds():
    slept = []
    made, session = collector(
        FakeResponse({}, status_code=429),
        rows(site_row(DAY, 7, 1, 1, 1, 1, 1)),
        rows(),
        rows(),
        sleep=slept.append,
    )

    day = made.collect_range(start=DAY, end=DAY).days[DAY]

    assert day.sessions == 7
    assert slept, "a rate limit must be waited out, not hammered"


def test_a_refused_request_is_not_retried():
    """A 400 means this application asked for something GA4 will never answer.
    Repeating it turns one clear failure into four identical ones."""
    made, session = collector(FakeResponse({}, status_code=400))

    with pytest.raises(Ga4ResponseError) as error:
        made.collect_range(start=DAY, end=DAY)

    assert len(session.requests) == 1
    assert "400" in str(error.value)


def test_retries_are_bounded():
    made, session = collector(*[FakeResponse({}, status_code=503) for _ in range(4)])

    with pytest.raises(Ga4ResponseError):
        made.collect_range(start=DAY, end=DAY)

    assert len(session.requests) == 4


def test_an_error_never_carries_google_s_response_body():
    """The body names the property and, on an auth failure, part of the
    credential."""
    made, _ = collector(FakeResponse({"error": {"message": "property 123456789 denied"}}, 403))

    with pytest.raises(Ga4ResponseError) as error:
        made.collect_range(start=DAY, end=DAY)

    assert "123456789" not in str(error.value)
    assert "denied" not in str(error.value)


# -- the request itself --------------------------------------------------


def test_the_reports_ask_for_the_dimensions_the_storage_is_keyed_on():
    made, session = collector(rows(), rows(), rows())

    made.collect_range(start=DAY, end=NEXT)

    site, pages, channels = session.requests
    assert [d["name"] for d in site["dimensions"]] == ["date"]
    assert [d["name"] for d in pages["dimensions"]] == ["date", "pagePath"]
    assert [d["name"] for d in channels["dimensions"]] == ["date", "sessionDefaultChannelGroup"]
    assert site["dateRanges"] == [{"startDate": "2026-08-08", "endDate": "2026-08-09"}]


def test_no_request_asks_for_anything_that_identifies_a_person():
    made, session = collector(rows(), rows(), rows())

    made.collect_range(start=DAY, end=DAY)

    asked = str(session.requests)
    for forbidden in ("clientId", "userId", "streamId", "audience", "city", "region"):
        assert forbidden not in asked


def test_a_reversed_range_is_refused():
    made, _ = collector()

    with pytest.raises(ValueError):
        made.collect_range(start=NEXT, end=DAY)
