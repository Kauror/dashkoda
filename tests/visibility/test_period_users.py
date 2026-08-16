"""Period user counts: the figure that is fetched rather than derived.

The rule this feature exists to respect is that distinct people do not add
across days. So the interesting assertions are not "does it store a number" but
the three ways it could quietly produce a wrong one: summing the daily rows,
answering a window nobody asked GA4 about, and turning "no answer" into a zero.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.visibility.ga4 import Ga4ApiCollector, Ga4Configuration, Ga4ResponseError
from apps.visibility.models import Ga4PeriodUsers
from apps.visibility.period_users import (
    DateRange,
    get_period_users,
    periods_to_fetch,
    record_period_users,
    synchronize_period_users,
)
from apps.visibility.website_page import FOCUS_OVERVIEW, build_website_page

from .conftest import END, PREV_END, PREV_START, START

# No module-level `django_db`: the four collector tests below take their session
# and need neither a database nor a credential, so they stay runnable where one
# is not available. Everything that reads coverage or stores a row marks itself.

CONFIGURED = Ga4Configuration(property_id="123456789", credentials_file="/run/secrets/ga4.json")


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def post(self, url, json=None, timeout=None):
        self.requests.append(json)
        return self._responses.pop(0)


class FakeCollector:
    """Answers every window with the same count unless told otherwise."""

    def __init__(self, answers=None, default=500):
        self.answers = answers or {}
        self.default = default
        self.asked: list[tuple[dt.date, dt.date]] = []

    def collect_period_users(self, *, start, end):
        self.asked.append((start, end))
        return self.answers.get((start, end), self.default)


# ---------------------------------------------------------------------------
# The GA4 request
# ---------------------------------------------------------------------------


def test_the_period_query_carries_no_date_dimension():
    """The whole point. A `date` dimension would return daily rows again."""
    session = FakeSession(
        [FakeResponse({"rowCount": 1, "rows": [{"metricValues": [{"value": "4210"}]}]})]
    )
    collector = Ga4ApiCollector(CONFIGURED, session=session)

    users = collector.collect_period_users(start=START, end=END)

    assert users == 4210
    (body,) = session.requests
    assert "dimensions" not in body
    assert body["metrics"] == [{"name": "activeUsers"}]
    assert body["dateRanges"] == [{"startDate": START.isoformat(), "endDate": END.isoformat()}]


def test_a_range_with_no_row_is_an_absence_not_a_zero():
    session = FakeSession([FakeResponse({"rowCount": 0})])
    collector = Ga4ApiCollector(CONFIGURED, session=session)

    assert collector.collect_period_users(start=START, end=END) is None


def test_a_negative_user_count_is_refused():
    session = FakeSession(
        [FakeResponse({"rowCount": 1, "rows": [{"metricValues": [{"value": "-3"}]}]})]
    )
    collector = Ga4ApiCollector(CONFIGURED, session=session)

    with pytest.raises(Ga4ResponseError):
        collector.collect_period_users(start=START, end=END)


def test_a_backwards_range_is_refused_before_it_is_asked():
    session = FakeSession([])
    collector = Ga4ApiCollector(CONFIGURED, session=session)

    with pytest.raises(ValueError):
        collector.collect_period_users(start=END, end=START)
    assert session.requests == []


# ---------------------------------------------------------------------------
# Which windows are fetched
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_nothing_is_fetched_before_anything_is_collected():
    assert periods_to_fetch() == ()


@pytest.mark.django_db
def test_the_selectable_windows_and_their_comparisons_are_fetched(history):
    windows = periods_to_fetch()

    assert DateRange(start=START, end=END) in windows
    assert DateRange(start=PREV_START, end=PREV_END) in windows


@pytest.mark.django_db
def test_windows_that_clamp_together_cost_one_request(history):
    """A young property resolves several presets to the same dates.

    Sixty days of history means `1 aasta`, `3 aastat`, `5 aastat` and `Kõik`
    are all the same window. Asking GA4 four times for one answer is the waste
    this de-duplication exists to prevent.
    """
    windows = periods_to_fetch()

    assert len(windows) == len(set(windows))


# ---------------------------------------------------------------------------
# Storing
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_an_unfetched_window_reads_as_none_not_zero():
    assert get_period_users(START, END) is None


@pytest.mark.django_db
def test_a_stored_answer_is_replaced_rather_than_versioned():
    assert record_period_users(START, END, 100) is True
    assert record_period_users(START, END, 100) is False
    assert record_period_users(START, END, 140) is True

    assert Ga4PeriodUsers.objects.filter(start_date=START, end_date=END).count() == 1
    assert get_period_users(START, END) == 140


@pytest.mark.django_db
def test_an_unanswered_window_stores_nothing(history):
    collector = FakeCollector(default=None)

    summary = synchronize_period_users(collector)

    assert summary.fetched > 0
    assert summary.empty == summary.fetched
    assert Ga4PeriodUsers.objects.count() == 0


@pytest.mark.django_db
def test_a_dry_run_asks_and_stores_nothing(history):
    collector = FakeCollector()

    summary = synchronize_period_users(collector, dry_run=True)

    assert summary.fetched > 0
    assert Ga4PeriodUsers.objects.count() == 0


@pytest.mark.django_db
def test_the_sync_stores_one_row_per_window(history):
    collector = FakeCollector()

    summary = synchronize_period_users(collector)

    assert Ga4PeriodUsers.objects.count() == summary.stored == len(collector.asked)


# ---------------------------------------------------------------------------
# What reaches the page
# ---------------------------------------------------------------------------


def digits(value: str) -> str:
    """`integer` groups thousands with a non-breaking space; compare the digits."""
    return "".join(character for character in value if character.isdigit())


@pytest.mark.django_db
def test_the_users_card_leads_the_strip_with_the_fetched_figure(history):
    record_period_users(START, END, 4210)
    record_period_users(PREV_START, PREV_END, 4000)

    page = build_website_page(focus_key=FOCUS_OVERVIEW, period_key="30")
    users = page.headlines[0]

    assert users.label == "Kasutajad"
    assert digits(users.value) == "4210"
    assert users.has_change


@pytest.mark.django_db
def test_the_users_card_is_not_the_sum_of_the_daily_counts(history):
    """The failure this whole module exists to prevent.

    `history` publishes thirty days of 800-plus active users. A page that added
    them would show something over 24 000; the fetched answer is 4 210 and that
    is what has to appear.
    """
    record_period_users(START, END, 4210)

    page = build_website_page(focus_key=FOCUS_OVERVIEW, period_key="30")

    assert digits(page.headlines[0].value) == "4210"


@pytest.mark.django_db
def test_an_unfetched_window_shows_a_reason_rather_than_a_number(history):
    page = build_website_page(focus_key=FOCUS_OVERVIEW, period_key="30")
    users = page.headlines[0]

    assert users.label == "Kasutajad"
    assert not users.has_value
    assert users.note


@pytest.mark.django_db
def test_a_custom_range_says_it_was_never_asked(history):
    page = build_website_page(
        focus_key=FOCUS_OVERVIEW,
        period_key="kohandatud",
        date_from=START.isoformat(),
        date_to=(START + dt.timedelta(days=13)).isoformat(),
    )
    users = page.headlines[0]

    assert not users.has_value
    assert "kohta ei ole kasutajate arvu päritud" in users.note
