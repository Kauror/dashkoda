"""Scheduled matching of open legal records against the current catalogue.

Reads PostgreSQL only; nothing here contacts Koda.ee. The decisions are written
to their own immutable snapshot, and the Õigusloome page turns the `matched`
ones into links the next time it renders. Nothing is written back onto an
imported legal row.

The run identity is the exact legal snapshot, the exact catalogue snapshot and
the matcher version. Running it twice over unchanged inputs recomputes nothing
and reports `unchanged`; changing the matcher version produces a new snapshot
and retires the previous one.

Exit codes:

    0  imported, unchanged, or a successful dry run
    1  failed
    3  another match run was already running
"""

from django.core.management.base import BaseCommand

from apps.core.feed_commands import FeedCommandOutputMixin
from apps.core.feeds import FeedLocked, advisory_lock
from apps.legal_work.current_topic_match_sync import LOCK_NAME, run_current_topic_matching
from apps.legal_work.models import SyncResult


class Command(FeedCommandOutputMixin, BaseCommand):
    help = (
        "Match the current legal-work snapshot's open records against the "
        "current Koda.ee 'Hetkel käsil' catalogue and publish the decisions."
    )

    def add_arguments(self, parser):
        self.add_output_arguments(
            parser, dry_run_help="Compute every decision without publishing a snapshot."
        )

    def handle(self, *args, **options):
        as_json = options["as_json"]
        try:
            # Its own lock, separate from the collector's: a slow catalogue
            # crawl must not stop matching, and neither may collide with the
            # workbook synchronisation.
            with advisory_lock(LOCK_NAME):
                outcome = run_current_topic_matching(dry_run=options["dry_run"])
        except FeedLocked as error:
            self.exit_locked(error, as_json=as_json)

        # Only the keys `MatchOutcomeReport.as_dict` defines: result,
        # detail, dry-run flag, snapshot id, four counts and the matcher
        # version. No topic, no candidate title, no URL, no evidence text.
        payload = outcome.as_dict()
        if outcome.result == SyncResult.FAILED:
            self.emit(as_json, payload, outcome.detail, style=self.style.ERROR)
            raise SystemExit(outcome.exit_code)

        message = outcome.detail
        if outcome.result == SyncResult.IMPORTED:
            message = (
                f"{outcome.detail} Kirjeid: {outcome.legal_item_count}. "
                f"Seotud: {outcome.matched_count}, ebaselgeid: {outcome.ambiguous_count}, "
                f"sidumata: {outcome.unmatched_count}."
            )
        self.emit(as_json, payload, message, style=self.style.SUCCESS)
