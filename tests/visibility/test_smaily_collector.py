"""The Smaily collector: what it asks for, and every shape it can be answered with.

No database and no credential. `SmailyApiClient` takes its session, so every
response Smaily can produce — including the ones that must raise — is reachable
here without a network.

The subscriber counts below are the shape of the Chamber's real lists but not
their values. A public test suite is a poor place to keep either the account's
segment sizes or its subdomain.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.visibility.smaily import (
    MAX_ATTEMPTS,
    SegmentReading,
    SegmentRow,
    SmailyApiClient,
    SmailyConfiguration,
    SmailyNotConfigured,
    SmailyResponseError,
)

DAY = dt.date(2026, 8, 10)

CONFIGURED = SmailyConfiguration(
    subdomain="example",
    username="test-user",
    password="test-password-not-real",
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    """Answers each `GET` from a queue and records what was asked."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []
        self.auth = None

    def get(self, url, params=None, timeout=None, allow_redirects=None):
        self.calls.append({"url": url, "params": params or {}})
        if not self._responses:
            raise AssertionError("collector made more requests than the test provided")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def client(*responses, configuration=CONFIGURED):
    return SmailyApiClient(configuration, session=FakeSession(*responses))


SEGMENTS = [
    {"id": 2690, "name": "E-teataja list", "subscribers_count": 100},
    {"id": 2691, "name": "E-teataja list mitteliikmed", "subscribers_count": 200},
    {"id": 2711, "name": "E-News list", "subscribers_count": 30},
    {"id": 2692, "name": "E-vestnik list - liikmed ja mitteliikmed koos", "subscribers_count": 40},
]


# -- configuration ----------------------------------------------------------


def test_incomplete_configuration_names_what_is_missing_and_no_values():
    configuration = SmailyConfiguration(subdomain="", username="", password="secret-value")
    with pytest.raises(SmailyNotConfigured) as error:
        configuration.require()
    message = str(error.value)
    assert "SMAILY_SUBDOMAIN" in message
    assert "SMAILY_API_USERNAME" in message
    # The one value that *is* set must not be echoed back.
    assert "secret-value" not in message


def test_a_subdomain_that_is_not_a_dns_label_is_refused():
    """A crafted subdomain must not be able to move the request to another host.

    The `Authorization` header goes wherever the URL points, so this is the
    difference between a misconfiguration and handing the credential away.
    """
    for hostile in ("evil.example/", "a.b", "has space", "-leading", "trailing-"):
        configuration = SmailyConfiguration(subdomain=hostile, username="u", password="p")
        with pytest.raises(SmailyNotConfigured):
            _ = configuration.api_root


def test_api_root_is_https_on_the_account_host():
    assert CONFIGURED.api_root == "https://example.sendsmaily.net/api"


# -- the happy path ---------------------------------------------------------


def test_segments_are_normalised_into_rows():
    collector = client(FakeResponse(SEGMENTS))
    reading = collector.collect_segments(observed_on=DAY)

    assert reading.observed_on == DAY
    assert len(reading.segments) == 4
    assert reading.by_id()[2690].name == "E-teataja list"
    assert reading.by_id()[2691].subscribers == 200
    assert collector.counts.requests == 1
    assert collector.counts.segment_rows == 4


def test_the_request_is_a_get_to_the_list_endpoint():
    collector = client(FakeResponse(SEGMENTS))
    collector.collect_segments(observed_on=DAY)
    call = collector._session.calls[0]
    assert call["url"] == "https://example.sendsmaily.net/api/list.php"


def test_counts_arrive_as_strings_too():
    """Smaily has returned numbers as JSON strings; both must parse."""
    collector = client(FakeResponse([{"id": "2690", "name": "x", "subscribers_count": "8008"}]))
    reading = collector.collect_segments(observed_on=DAY)
    assert reading.segments[0].segment_id == 2690
    assert reading.segments[0].subscribers == 8008


def test_a_pagination_envelope_is_accepted():
    """An API that gains an envelope must not read as "the Chamber has no lists"."""
    collector = client(FakeResponse({"segments": SEGMENTS}))
    assert len(collector.collect_segments(observed_on=DAY).segments) == 4


# -- refusals ---------------------------------------------------------------


def test_a_response_carrying_recipient_detail_is_refused_and_not_echoed():
    """The one response this integration must never store.

    Nothing here asks for `detailed=1`. If Smaily ever returns recipient rows
    anyway, the collector stops rather than parsing personal data into a
    database with no field for it — and the error must not quote the body.
    """
    body = {"addresses": [{"email": "person@example.org"}]}
    collector = client(FakeResponse(body))
    with pytest.raises(SmailyResponseError) as error:
        collector.collect_segments(observed_on=DAY)
    assert "person@example.org" not in str(error.value)


def test_an_unreadable_body_is_refused():
    collector = client(FakeResponse(ValueError("not json")))
    with pytest.raises(SmailyResponseError):
        collector.collect_segments(observed_on=DAY)


def test_a_segment_without_a_count_is_refused():
    collector = client(FakeResponse([{"id": 1, "name": "x"}]))
    with pytest.raises(SmailyResponseError):
        collector.collect_segments(observed_on=DAY)


def test_a_non_list_response_is_refused():
    collector = client(FakeResponse("unexpected"))
    with pytest.raises(SmailyResponseError):
        collector.collect_segments(observed_on=DAY)


def test_a_rejected_credential_fails_immediately_without_retrying():
    """Retrying a bad password just makes the same mistake against someone
    else's account four times."""
    collector = client(FakeResponse({}, status_code=401))
    with pytest.raises(SmailyResponseError):
        collector.collect_segments(observed_on=DAY)
    assert collector.counts.requests == 1
    assert collector.counts.retries == 0


def test_a_rate_limit_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr("apps.visibility.smaily.time.sleep", lambda _seconds: None)
    collector = client(FakeResponse({}, status_code=429), FakeResponse(SEGMENTS))
    reading = collector.collect_segments(observed_on=DAY)
    assert len(reading.segments) == 4
    assert collector.counts.retries == 1


def test_retries_are_bounded(monkeypatch):
    monkeypatch.setattr("apps.visibility.smaily.time.sleep", lambda _seconds: None)
    collector = client(*[FakeResponse({}, status_code=503) for _ in range(MAX_ATTEMPTS)])
    with pytest.raises(SmailyResponseError):
        collector.collect_segments(observed_on=DAY)
    assert collector.counts.requests == MAX_ATTEMPTS


def test_a_transport_failure_does_not_leak_the_request_url():
    """A `requests` exception carries the URL, which names the account."""
    import requests

    collector = client(requests.ConnectionError("failed to connect to example.sendsmaily.net"))
    with pytest.raises(SmailyResponseError) as error:
        collector.collect_segments(observed_on=DAY)
    assert "sendsmaily.net" not in str(error.value)
    assert "example" not in str(error.value)


def test_an_unknown_endpoint_cannot_be_requested():
    collector = client()
    with pytest.raises(SmailyResponseError):
        collector._get("subscribers.php")


# -- the reading ------------------------------------------------------------


def test_a_duplicated_segment_is_refused():
    reading = SegmentReading(
        observed_on=DAY,
        segments=(SegmentRow(1, "a", 1), SegmentRow(1, "a", 2)),
    )
    with pytest.raises(SmailyResponseError):
        reading.validate()


def test_the_canonical_payload_is_order_independent():
    """Smaily reordering its segments is not a list that changed size."""
    one = SegmentReading(observed_on=DAY, segments=(SegmentRow(1, "a", 10), SegmentRow(2, "b", 20)))
    other = SegmentReading(
        observed_on=DAY, segments=(SegmentRow(2, "b", 20), SegmentRow(1, "a", 10))
    )
    assert one.canonical_payload() == other.canonical_payload()


# -- campaigns --------------------------------------------------------------

CAMPAIGNS = [
    {
        "id": 4421,
        "name": "E-Teataja: Riigipiiri kaitserajatiste alused",
        "template": {"id": 9, "name": "e-Teataja 4.08 mitteliikmed", "preview_url": "x"},
        "tags": [],
        "status": "COMPLETED",
        "created_at": "2026-08-04 10:24:03",
        "completed_at": "2026-08-04 11:02:11",
    }
]

STATS = {
    "name": "Kaubanduskoja sündmuste kalender",
    "status": "COMPLETED",
    "total_count": 5235,
    "delivered_count": 5167,
    "bounce_count": 68,
    "opened_count": 2628,
    "opened_percent": 50.9,
    "click_count": 4402,
    "unique_click_count": 453,
    "view_count": 4897,
    "unique_view_count": 2462,
    "unsubscribe_count": 4,
    "complaint_count": 0,
    "forward_count": 0,
}


def test_campaigns_are_normalised_with_their_template_name():
    collector = client(FakeResponse(CAMPAIGNS))
    rows = collector.collect_campaigns()

    assert len(rows) == 1
    row = rows[0]
    assert row.campaign_id == 4421
    assert row.template_name == "e-Teataja 4.08 mitteliikmed"
    assert row.status == "COMPLETED"
    assert row.completed_at is not None
    assert row.completed_at.year == 2026


def test_only_completed_campaigns_are_asked_for():
    """A draft has no statistics and a cancelled campaign was never sent."""
    collector = client(FakeResponse(CAMPAIGNS))
    collector.collect_campaigns()
    assert collector._session.calls[0]["params"]["status"] == "COMPLETED"


def test_the_campaign_list_is_always_bounded():
    """`limit=0` means "every campaign ever" to Smaily. Never sent."""
    collector = client(FakeResponse(CAMPAIGNS))
    collector.collect_campaigns(limit=25)
    assert collector._session.calls[0]["params"]["limit"] == 25

    with pytest.raises(SmailyResponseError):
        client(FakeResponse(CAMPAIGNS)).collect_campaigns(limit=0)


def test_statistics_are_aggregate_counts():
    collector = client(FakeResponse(STATS))
    row = collector.collect_campaign_stats(4423)

    assert row.campaign_id == 4423
    assert row.delivered_count == 5167
    assert row.opened_count == 2628
    assert row.unique_click_count == 453
    assert row.has_any_figure


def test_the_statistics_request_never_asks_for_recipient_detail():
    """`detailed` is not sent at all, so no typo can flip it to 1."""
    collector = client(FakeResponse(STATS))
    collector.collect_campaign_stats(4423)
    params = collector._session.calls[0]["params"]
    assert params == {"id": 4423}
    assert "detailed" not in params


def test_a_percentage_smaily_reports_is_not_carried_into_the_row():
    """Rates are derived from counts, with a named denominator, not stored."""
    collector = client(FakeResponse(STATS))
    row = collector.collect_campaign_stats(4423)
    assert not hasattr(row, "opened_percent")
    assert "opened_percent" not in row.payload()


def test_a_missing_count_stays_absent_rather_than_becoming_zero():
    collector = client(FakeResponse({"total_count": 10}))
    row = collector.collect_campaign_stats(1)
    assert row.total_count == 10
    assert row.delivered_count is None
    assert row.opened_count is None


def test_statistics_carrying_recipient_rows_are_refused():
    collector = client(FakeResponse({**STATS, "addresses": [{"email": "a@example.org"}]}))
    with pytest.raises(SmailyResponseError) as error:
        collector.collect_campaign_stats(4423)
    assert "a@example.org" not in str(error.value)


def test_a_campaign_without_an_identifier_is_refused():
    collector = client(FakeResponse([{"name": "no id"}]))
    with pytest.raises(SmailyResponseError):
        collector.collect_campaigns()


def test_an_unreadable_campaign_date_is_refused_rather_than_guessed():
    collector = client(FakeResponse([{"id": 1, "completed_at": "eile"}]))
    with pytest.raises(SmailyResponseError):
        collector.collect_campaigns()
