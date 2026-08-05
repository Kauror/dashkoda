"""Scheduled shadow matching of open legal records against the catalogue.

Reads PostgreSQL only. Nothing here contacts Koda.ee, and nothing here changes
what a viewer sees: the decisions are written to their own immutable snapshot
and inspected in the admin while the thresholds are calibrated.

The run identity is the exact legal snapshot, the exact catalogue snapshot and
the matcher version. Running it twice over unchanged inputs recomputes nothing
and reports `unchanged`; changing the matcher version produces a new snapshot
and retires the previous one.

Exit codes:

    0  imported, unchanged, or a successful dry run
    1  failed
    3  another match run was already running
"""

import json

from django.core.management.base import BaseCommand

from apps.core.feeds import FeedLocked, advisory_lock
from apps.legal_work.current_topic_match_sync import LOCK_NAME, run_current_topic_matching
from apps.legal_work.models import SyncResult
from apps.legal_work.sync import EXIT_LOCKED


class Command(BaseCommand):
    help = (
        "Match the current legal-work snapshot's open records against the "
        "current Koda.ee 'Hetkel käsil' catalogue and publish the decisions."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute every decision without publishing a snapshot.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Emit one structured JSON line instead of prose.",
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
            self._emit(
                as_json,
                {"result": "locked", "detail": str(error)},
                f"Vahele jäetud: {error}",
                style=self.style.WARNING,
            )
            raise SystemExit(EXIT_LOCKED) from None

        payload = outcome.as_dict()
        if outcome.result == SyncResult.FAILED:
            self._emit(as_json, payload, outcome.detail, style=self.style.ERROR)
            raise SystemExit(outcome.exit_code)

        message = outcome.detail
        if outcome.result == SyncResult.IMPORTED:
            message = (
                f"{outcome.detail} Kirjeid: {outcome.legal_item_count}. "
                f"Seotud: {outcome.matched_count}, ebaselgeid: {outcome.ambiguous_count}, "
                f"sidumata: {outcome.unmatched_count}."
            )
        self._emit(as_json, payload, message, style=self.style.SUCCESS)

    def _emit(self, as_json: bool, payload: dict, message: str, *, style) -> None:
        if as_json:
            # Only the keys `MatchOutcomeReport.as_dict` defines: result,
            # detail, dry-run flag, snapshot id, four counts and the matcher
            # version. No topic, no candidate title, no URL, no evidence text.
            self.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            self.stdout.write(style(message))
