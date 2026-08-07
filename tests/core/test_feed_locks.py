"""The advisory locks, after three implementations became one.

`legal_work` and `event_programme` each carried a character-for-character copy
of the key derivation and the lock helper. Consolidating them onto
`apps.core.feeds` is only safe if the keys do not move: an advisory lock is
identified by a 64-bit integer, and a refactor that changed one would silently
stop protecting a production feed while every test still passed.

So the keys are written down here as literals. They were computed from the
pre-refactor implementation and must never change again — a feed whose key moves
can overlap with a run started before the deploy.
"""

from __future__ import annotations

import hashlib

import pytest

from apps.core.feeds import FeedLocked, advisory_lock, advisory_lock_key
from apps.event_programme.sync import ADVISORY_LOCK_NAMESPACE as EVENT_PROGRAMME
from apps.legal_work.archived_topic_sync import LOCK_NAME as ARCHIVED_TOPICS
from apps.legal_work.current_topic_sync import LOCK_NAME as CURRENT_TOPICS
from apps.legal_work.opinion_match_sync import LOCK_NAME as OPINION_MATCH
from apps.legal_work.sync import ADVISORY_LOCK_NAMESPACE as LEGAL_WORK
from apps.visibility.ga4_sync import LOCK_NAME as GA4

#: Name → key, as the pre-consolidation code derived them.
PRODUCTION_KEYS = {
    LEGAL_WORK: -731543855494011862,
    EVENT_PROGRAMME: -5220023887515080790,
    CURRENT_TOPICS: 7404925716156652591,
    GA4: 8986780637470272167,
}


class TestTheKeysDidNotMove:
    @pytest.mark.parametrize(("name", "expected"), sorted(PRODUCTION_KEYS.items()))
    def test_each_feeds_key_is_unchanged(self, name, expected):
        assert advisory_lock_key(name) == expected

    def test_the_derivation_is_the_documented_one(self):
        """Spelled out, so a future edit has to disagree with this on purpose."""
        name = "dashkoda.legal_work.sync_oigusloome"
        expected = int.from_bytes(
            hashlib.sha256(name.encode("utf-8")).digest()[:8], "big", signed=True
        )

        assert advisory_lock_key(name) == expected

    def test_every_feed_has_a_key_of_its_own(self):
        names = [LEGAL_WORK, EVENT_PROGRAMME, CURRENT_TOPICS, ARCHIVED_TOPICS, OPINION_MATCH, GA4]
        keys = {advisory_lock_key(name) for name in names}

        assert len(keys) == len(names), "two feeds would block each other"


class TestThereIsOneImplementation:
    """The copies are gone, and the wrappers are wrappers."""

    def test_the_feed_wrappers_delegate_to_the_shared_key_function(self):
        from apps.event_programme import sync as event_sync
        from apps.legal_work import sync as legal_sync

        assert legal_sync.advisory_lock_key() == advisory_lock_key(LEGAL_WORK)
        assert event_sync.advisory_lock_key() == advisory_lock_key(EVENT_PROGRAMME)

    def test_neither_module_still_derives_a_key_itself(self):
        """No second lock implementation, and no second key derivation.

        Checked as "does not import `hashlib`" rather than "does not contain
        `sha256`": both modules legitimately read `download.sha256`, the
        workbook's own content checksum, which has nothing to do with a lock.
        Deriving a key needs `hashlib`, and neither module imports it any more.
        """
        import ast
        import inspect

        from apps.event_programme import sync as event_sync
        from apps.legal_work import sync as legal_sync

        for module in (legal_sync, event_sync):
            source = inspect.getsource(module)
            assert "pg_try_advisory_lock" not in source, "a second lock implementation is back"

            imported = set()
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
            assert "hashlib" not in imported, "a second key derivation is back"

    def test_the_feed_exception_is_the_shared_one(self):
        """`except SyncLocked` must catch what the shared helper raises."""
        from apps.event_programme.sync import SyncLocked as EventLocked
        from apps.legal_work.sync import SyncLocked as LegalLocked

        assert LegalLocked is FeedLocked
        assert EventLocked is FeedLocked


@pytest.mark.django_db(transaction=True)
class TestTheContentionMessageIsUnchanged:
    """The message reaches a cron log and a command's locked JSON `detail`.

    A consolidation that quietly reworded it would be output drift, so each feed
    still supplies the wording it had before.
    """

    def _refused(self, take_lock):
        from concurrent.futures import ThreadPoolExecutor

        from django.db import close_old_connections

        def attempt():
            close_old_connections()
            try:
                with take_lock():
                    return None
            except FeedLocked as error:
                return str(error)
            finally:
                close_old_connections()

        with take_lock():
            with ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(attempt).result()

    def test_the_legal_work_wording_is_preserved(self):
        from apps.legal_work.sync import advisory_lock as legal_lock

        assert self._refused(legal_lock) == "Teine sünkroonimine juba käib."

    def test_the_event_programme_wording_is_preserved(self):
        from apps.event_programme.sync import advisory_lock as event_lock

        assert self._refused(event_lock) == "Teine sünkroonimine juba käib."

    def test_a_feed_without_its_own_wording_gets_the_shared_one(self):
        def take():
            return advisory_lock(CURRENT_TOPICS)

        assert self._refused(take) == f"Allika {CURRENT_TOPICS} sünkroonimine juba käib."


@pytest.mark.django_db(transaction=True)
def test_the_lock_is_still_refused_across_connections_and_released_after():
    """The guarantee the whole mechanism exists for, through the shared helper."""
    from concurrent.futures import ThreadPoolExecutor

    from django.db import close_old_connections

    name = "dashkoda.test.shared_mechanics"

    def attempt():
        close_old_connections()
        try:
            with advisory_lock(name):
                return "acquired"
        except FeedLocked:
            return "refused"
        finally:
            close_old_connections()

    with advisory_lock(name):
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(attempt).result() == "refused"

    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(attempt).result() == "acquired"
