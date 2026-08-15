"""The row-level directory collector and its carry-forward register.

The normalisation tests need no PostgreSQL. The reconciliation tests do, and
they are the ones that matter: the register's whole contract is that a member
who disappears is marked rather than deleted, and that one who comes back keeps
the date it first appeared.
"""

from __future__ import annotations

import json

import pytest

from apps.core.feeds import FeedResult
from apps.membership.collector import MembershipCollectionError
from apps.membership.directory_collector import (
    DirectoryCollection,
    DirectoryRow,
    normalise,
)
from apps.membership.directory_sync import reconcile_entries, synchronize_member_directory

BASE = "https://www.koda.ee/et/liige/"


def payload(*pairs) -> bytes:
    return json.dumps([{"crn": code, "url": f"{BASE}{slug}"} for code, slug in pairs]).encode()


def collection_of(*pairs, etag="", last_modified="") -> DirectoryCollection:
    from apps.core.canonical import canonical_checksum

    rows, _duplicates, _rejected = normalise(payload(*pairs))
    canonical = {
        "dataset": "koda-member-directory",
        "schema_version": "1.0",
        "entries": [[row.registry_code, row.profile_path] for row in rows],
    }
    checksum, size = canonical_checksum(canonical)
    return DirectoryCollection(
        rows=rows,
        sha256=checksum,
        size_bytes=size,
        canonical=canonical,
        etag=etag,
        last_modified=last_modified,
        duplicate_identities=0,
        rejected_rows=0,
    )


# ---------------------------------------------------------------------------
# Normalisation — no database
# ---------------------------------------------------------------------------


def test_rows_are_reduced_to_a_code_and_a_path():
    rows, duplicates, rejected = normalise(payload(("12765966", "heisi-it-ou")))
    assert rows == (DirectoryRow(registry_code="12765966", profile_path="/et/liige/heisi-it-ou"),)
    assert (duplicates, rejected) == (0, 0)


def test_rows_are_sorted_so_the_digest_describes_a_set_not_an_order():
    """The endpoint's row order drifts between responses.

    Hashing it would republish an identical set every morning, which is the
    exact failure `canonical.py` exists to prevent.
    """
    first = collection_of(("22222222", "b"), ("11111111", "a"))
    second = collection_of(("11111111", "a"), ("22222222", "b"))
    assert first.sha256 == second.sha256
    assert [row.registry_code for row in first.rows] == ["11111111", "22222222"]


def test_a_url_on_another_host_is_rejected():
    content = json.dumps([{"crn": "11111111", "url": "https://example.invalid/et/liige/x"}])
    with pytest.raises(MembershipCollectionError):
        normalise(content.encode())


def test_a_duplicate_code_is_counted_once():
    rows, duplicates, _rejected = normalise(
        payload(("11111111", "a"), ("11111111", "a-again"), ("22222222", "b"))
    )
    assert len(rows) == 2
    assert duplicates == 1


def test_an_empty_list_is_refused():
    with pytest.raises(MembershipCollectionError, match="tühi"):
        normalise(b"[]")


def test_malformed_json_is_refused_without_quoting_the_body():
    with pytest.raises(MembershipCollectionError) as error:
        normalise(b"{not json")
    assert "JSONDecodeError" in str(error.value)


# ---------------------------------------------------------------------------
# Reconciliation — PostgreSQL
# ---------------------------------------------------------------------------


@pytest.fixture
def directory_source(db):
    from apps.membership.bootstrap import ensure_member_directory_source

    return ensure_member_directory_source()


@pytest.mark.django_db
def test_a_first_run_creates_every_entry(directory_source):
    from apps.membership.models import MemberDirectoryEntry

    result = reconcile_entries(
        directory_source, collection_of(("11111111", "a"), ("22222222", "b")).rows
    )
    assert (result.added, result.unpublished) == (2, 0)
    assert MemberDirectoryEntry.objects.filter(is_published=True).count() == 2


@pytest.mark.django_db
def test_reapplying_the_same_set_changes_only_last_seen(directory_source):
    from apps.membership.models import MemberDirectoryEntry

    rows = collection_of(("11111111", "a"), ("22222222", "b")).rows
    reconcile_entries(directory_source, rows)
    before = MemberDirectoryEntry.objects.get(registry_code="11111111")

    again = reconcile_entries(directory_source, rows)
    after = MemberDirectoryEntry.objects.get(registry_code="11111111")

    assert not again.changed
    assert after.first_seen_at == before.first_seen_at
    assert after.last_seen_at >= before.last_seen_at
    assert MemberDirectoryEntry.objects.count() == 2


@pytest.mark.django_db
def test_a_member_that_disappears_is_marked_not_deleted(directory_source):
    from apps.membership.models import MemberDirectoryEntry

    reconcile_entries(directory_source, collection_of(("11111111", "a"), ("22222222", "b")).rows)
    result = reconcile_entries(directory_source, collection_of(("11111111", "a")).rows)

    assert result.unpublished == 1
    gone = MemberDirectoryEntry.objects.get(registry_code="22222222")
    assert not gone.is_published
    assert gone.unpublished_at is not None


@pytest.mark.django_db
def test_a_restored_member_keeps_the_date_it_first_appeared(directory_source):
    from apps.membership.models import MemberDirectoryEntry

    both = collection_of(("11111111", "a"), ("22222222", "b")).rows
    reconcile_entries(directory_source, both)
    first_seen = MemberDirectoryEntry.objects.get(registry_code="22222222").first_seen_at

    reconcile_entries(directory_source, collection_of(("11111111", "a")).rows)
    result = reconcile_entries(directory_source, both)

    restored = MemberDirectoryEntry.objects.get(registry_code="22222222")
    assert result.restored == 1
    assert restored.is_published
    assert restored.unpublished_at is None
    assert restored.first_seen_at == first_seen


@pytest.mark.django_db
def test_a_renamed_profile_corrects_the_path_rather_than_adding_a_member(directory_source):
    from apps.membership.models import MemberDirectoryEntry

    reconcile_entries(directory_source, collection_of(("11111111", "vana-nimi")).rows)
    result = reconcile_entries(directory_source, collection_of(("11111111", "uus-nimi")).rows)

    assert result.moved == 1
    assert MemberDirectoryEntry.objects.count() == 1
    assert MemberDirectoryEntry.objects.get().profile_path == "/et/liige/uus-nimi"


# ---------------------------------------------------------------------------
# The synchronisation as a whole
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_sync_publishes_and_a_second_identical_sync_reports_unchanged():
    from apps.membership.models import MemberDirectoryEntry

    def collector(**_kwargs):
        return collection_of(("11111111", "a"), ("22222222", "b"))

    first = synchronize_member_directory(collector=collector)
    second = synchronize_member_directory(collector=collector)

    assert first.result == FeedResult.IMPORTED
    assert second.result == FeedResult.UNCHANGED
    assert MemberDirectoryEntry.objects.filter(is_published=True).count() == 2


@pytest.mark.django_db
def test_a_returning_member_is_restored_even_though_the_bytes_are_unchanged():
    """The case the import key alone would get wrong.

    A member unpublished and then restored returns the directory to a set that
    has already been published, so the run is correctly `unchanged` — and the
    register still has to bring the row back. Gating the reconciliation on a new
    import run would leave the page showing a member as unlisted for ever.
    """
    from apps.membership.models import MemberDirectoryEntry

    both = collection_of(("11111111", "a"), ("22222222", "b"))
    one = collection_of(("11111111", "a"))

    synchronize_member_directory(collector=lambda **_k: both)
    synchronize_member_directory(collector=lambda **_k: one)
    assert not MemberDirectoryEntry.objects.get(registry_code="22222222").is_published

    outcome = synchronize_member_directory(collector=lambda **_k: both)
    assert outcome.result == FeedResult.UNCHANGED
    assert MemberDirectoryEntry.objects.get(registry_code="22222222").is_published


@pytest.mark.django_db
def test_a_304_reports_unchanged_without_touching_the_register():
    from apps.membership.models import MemberDirectoryEntry

    synchronize_member_directory(collector=lambda **_k: collection_of(("11111111", "a")))
    outcome = synchronize_member_directory(collector=lambda **_k: None)

    assert outcome.result == FeedResult.UNCHANGED
    assert MemberDirectoryEntry.objects.filter(is_published=True).count() == 1


@pytest.mark.django_db
def test_an_implausible_collapse_is_refused_and_keeps_the_register(settings):
    """Fail closed: unpublishing hundreds of rows on a bad fetch is expensive.

    The same guard the member count uses, and for the same reason — a movement
    this large is far more likely to be a source fault than membership news.
    """
    from apps.membership.models import MemberDirectoryEntry, MembershipFeedState

    settings.KODA_MEMBERS_MAX_CHANGE_ABSOLUTE = 2
    settings.KODA_MEMBERS_MAX_CHANGE_RATIO = 0.15

    full = collection_of(*[(f"1000000{index}", f"m{index}") for index in range(10)])
    synchronize_member_directory(collector=lambda **_k: full)

    outcome = synchronize_member_directory(collector=lambda **_k: collection_of(("10000000", "m0")))

    assert outcome.result == FeedResult.FAILED
    assert MemberDirectoryEntry.objects.filter(is_published=True).count() == 10
    state = MembershipFeedState.objects.get(source__slug="koda-member-directory")
    assert state.last_result == FeedResult.FAILED


@pytest.mark.django_db
def test_a_collector_failure_never_escapes_and_never_empties_the_register():
    from apps.membership.models import MemberDirectoryEntry

    synchronize_member_directory(collector=lambda **_k: collection_of(("11111111", "a")))

    def broken(**_kwargs):
        raise MembershipCollectionError("Allikas ei vastanud.")

    outcome = synchronize_member_directory(collector=broken)
    assert outcome.result == FeedResult.FAILED
    assert MemberDirectoryEntry.objects.filter(is_published=True).count() == 1


@pytest.mark.django_db
def test_a_dry_run_writes_nothing():
    from apps.membership.models import MemberDirectoryEntry

    outcome = synchronize_member_directory(
        collector=lambda **_k: collection_of(("11111111", "a")), dry_run=True
    )
    assert outcome.dry_run
    assert not MemberDirectoryEntry.objects.exists()


@pytest.mark.django_db
def test_the_member_count_and_the_directory_are_separate_sources():
    """A failure on one side must be invisible to the other.

    They read the same endpoint, and keeping them apart is the whole reason the
    row-level work did not touch `collector.py`.
    """
    from apps.membership.models import MembershipCountObservation

    def broken(**_kwargs):
        raise MembershipCollectionError("Allikas ei vastanud.")

    synchronize_member_directory(collector=broken)
    assert not MembershipCountObservation.objects.exists()
