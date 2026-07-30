"""Counting the member directory, and what must never survive the count.

Every payload here is synthetic. No live member response is committed, and the
assertions prove that registration codes and profile URLs reach neither the
returned value nor the logs.
"""

from __future__ import annotations

import json
import logging

import pytest
from django.conf import settings

from apps.core.public_http import PublicFetchError
from apps.membership.collector import (
    MembershipCollectionError,
    collect_membership,
    is_change_plausible,
)

# Distinctive synthetic values the leak assertions look for.
SYNTHETIC_CRN = "99887766"
SYNTHETIC_SLUG = "sunteetiline-liige-mitte-paris"


def row(index: int) -> dict:
    return {
        "crn": f"{10000000 + index}",
        "url": f"https://www.koda.ee/et/liikmed/{SYNTHETIC_SLUG}-{index}",
    }


def payload(count: int = 5) -> bytes:
    return json.dumps([row(i) for i in range(count)]).encode("utf-8")


class FakeFetch:
    """Stands in for apps.core.public_http.fetch."""

    def __init__(self, content=b"", *, status=200, content_type="application/json", error=None):
        self.content = content
        self.status = status
        self.content_type = content_type
        self.error = error
        self.calls = 0
        self.seen_kwargs: list[dict] = []

    def __call__(self, url, **kwargs):
        self.calls += 1
        self.seen_kwargs.append(kwargs)
        if self.error is not None:
            raise self.error

        from apps.core.public_http import FetchResult

        return FetchResult(
            status_code=self.status,
            content=self.content,
            content_type=self.content_type,
            etag='"synthetic-etag"',
            last_modified="Thu, 30 Jul 2026 03:00:00 GMT",
            final_host="www.koda.ee",
        )


@pytest.fixture
def patch_fetch(monkeypatch):
    def apply(fake):
        monkeypatch.setattr("apps.membership.collector.fetch", fake)
        return fake

    return apply


# -- counting -----------------------------------------------------------


def test_a_valid_response_is_counted(patch_fetch):
    patch_fetch(FakeFetch(payload(7)))

    collection = collect_membership()

    assert collection.total_members == 7
    assert collection.duplicate_identities == 0
    assert collection.rejected_rows == 0
    assert len(collection.sha256) == 64


def test_the_checksum_covers_only_the_aggregate(patch_fetch):
    """Different rows, same count: the same content identity."""
    first = patch_fetch(FakeFetch(payload(4))) and collect_membership()

    shifted = json.dumps(
        [
            {"crn": f"{50000000 + i}", "url": f"https://www.koda.ee/et/liikmed/other-{i}"}
            for i in range(4)
        ]
    ).encode("utf-8")
    patch_fetch(FakeFetch(shifted))
    second = collect_membership()

    assert first.total_members == second.total_members == 4
    assert first.sha256 == second.sha256


def test_a_different_count_changes_the_checksum(patch_fetch):
    patch_fetch(FakeFetch(payload(4)))
    first = collect_membership()
    patch_fetch(FakeFetch(payload(5)))
    second = collect_membership()

    assert first.sha256 != second.sha256


def test_duplicate_identities_are_counted_once(patch_fetch):
    rows = [row(1), row(1), row(2)]
    patch_fetch(FakeFetch(json.dumps(rows).encode("utf-8")))

    collection = collect_membership()

    assert collection.total_members == 2
    assert collection.duplicate_identities == 1


def test_rows_without_an_identity_are_rejected(patch_fetch):
    rows = [row(1), {"crn": "", "url": "https://www.koda.ee/et/liikmed/x"}, row(2)]
    patch_fetch(FakeFetch(json.dumps(rows).encode("utf-8")))

    collection = collect_membership()

    assert collection.total_members == 2
    assert collection.rejected_rows == 1


def test_rows_with_an_off_domain_url_are_rejected(patch_fetch):
    rows = [row(1), {"crn": "123", "url": "https://example.invalid/member"}]
    patch_fetch(FakeFetch(json.dumps(rows).encode("utf-8")))

    collection = collect_membership()

    assert collection.total_members == 1
    assert collection.rejected_rows == 1


def test_mostly_malformed_rows_reject_the_whole_response(patch_fetch):
    rows = [row(1)] + [{"nope": True} for _ in range(5)]
    patch_fetch(FakeFetch(json.dumps(rows).encode("utf-8")))

    with pytest.raises(MembershipCollectionError, match="vigaseid ridu"):
        collect_membership()


# -- malformed sources --------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [b"{}", b'{"data": []}', b'"a string"', b"123"],
)
def test_a_non_array_top_level_is_rejected(patch_fetch, content):
    patch_fetch(FakeFetch(content))

    with pytest.raises(MembershipCollectionError, match="massiiv"):
        collect_membership()


def test_an_empty_array_is_rejected(patch_fetch):
    patch_fetch(FakeFetch(b"[]"))

    with pytest.raises(MembershipCollectionError, match="tühi"):
        collect_membership()


def test_malformed_json_is_rejected(patch_fetch):
    patch_fetch(FakeFetch(b"[{"))

    with pytest.raises(MembershipCollectionError, match="JSON"):
        collect_membership()


def test_a_transport_failure_becomes_a_collection_error(patch_fetch):
    patch_fetch(FakeFetch(error=PublicFetchError("Allikat ei leitud (404).")))

    with pytest.raises(MembershipCollectionError, match="404"):
        collect_membership()


def test_a_not_modified_response_returns_none(patch_fetch):
    patch_fetch(FakeFetch(b"", status=304))

    assert collect_membership(etag='"synthetic-etag"') is None


def test_conditional_headers_are_passed_through(patch_fetch):
    fake = patch_fetch(FakeFetch(payload(3)))

    collect_membership(etag='"e"', last_modified="Thu, 30 Jul 2026 03:00:00 GMT")

    assert fake.seen_kwargs[0]["etag"] == '"e"'
    assert fake.seen_kwargs[0]["last_modified"] == "Thu, 30 Jul 2026 03:00:00 GMT"


# -- nothing row-level survives -----------------------------------------


def test_no_registration_code_or_url_survives_collection(patch_fetch):
    rows = [{"crn": SYNTHETIC_CRN, "url": f"https://www.koda.ee/et/liikmed/{SYNTHETIC_SLUG}"}]
    patch_fetch(FakeFetch(json.dumps(rows).encode("utf-8")))

    collection = collect_membership()

    blob = repr(collection)
    assert SYNTHETIC_CRN not in blob
    assert SYNTHETIC_SLUG not in blob
    assert SYNTHETIC_CRN not in json.dumps(collection.canonical)
    assert SYNTHETIC_SLUG not in json.dumps(collection.canonical)
    assert set(collection.canonical) == {"dataset", "schema_version", "total_members"}


def test_nothing_row_level_reaches_the_log(patch_fetch, caplog):
    rows = [{"crn": SYNTHETIC_CRN, "url": f"https://www.koda.ee/et/liikmed/{SYNTHETIC_SLUG}"}]
    patch_fetch(FakeFetch(json.dumps(rows).encode("utf-8")))

    with caplog.at_level(logging.DEBUG, logger="dashkoda.membership.collector"):
        collect_membership()

    assert caplog.text
    assert SYNTHETIC_CRN not in caplog.text
    assert SYNTHETIC_SLUG not in caplog.text


# -- the change guard ---------------------------------------------------


def test_the_first_observation_always_publishes():
    assert is_change_plausible(None, 3395)[0] is True


def test_an_ordinary_movement_is_accepted():
    assert is_change_plausible(3395, 3400)[0] is True


def test_a_large_movement_is_refused():
    plausible, reason = is_change_plausible(3395, 1000)

    assert plausible is False
    assert "ebausutavalt" in reason


def test_a_large_ratio_below_the_absolute_floor_is_accepted():
    """A tiny directory must not trip the proportional rule alone."""
    assert is_change_plausible(10, 30)[0] is True


def test_a_large_absolute_change_below_the_ratio_is_accepted(settings):
    """A big directory must not trip the absolute rule alone."""
    settings.KODA_MEMBERS_MAX_CHANGE_ABSOLUTE = 200
    settings.KODA_MEMBERS_MAX_CHANGE_RATIO = 0.15

    assert is_change_plausible(100000, 100300)[0] is True


def test_no_member_count_is_hard_coded():
    """The guard must be relative, never anchored to today's number."""
    from apps.membership import collector

    source = (collector.__file__,)
    assert source  # sanity
    text = open(collector.__file__, encoding="utf-8").read()
    for suspicious in ("3395", "3394", "3396"):
        assert suspicious not in text


def test_the_thresholds_are_configuration_not_literals():
    assert settings.KODA_MEMBERS_MAX_CHANGE_RATIO > 0
    assert settings.KODA_MEMBERS_MAX_CHANGE_ABSOLUTE > 0
