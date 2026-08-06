"""Scheduled fallback matching against the archived consultation catalogue.

Reads PostgreSQL only; nothing here contacts Koda.ee. It considers exactly the
records the current matcher could not answer — consultation-eligible, and not
already matched against the live listing — and scores them over the archive
corpus under the archive's own thresholds.

The run identity is four-part: the legal snapshot, the archive snapshot, the
matcher version, and the current-topic match run this one deferred to. Any of
the four changing means a genuinely different question, so identical inputs
recompute nothing and report `unchanged`.

Exit codes:

    0  imported, unchanged, or a successful dry run
    1  failed, or a required current snapshot was missing
    3  another match run was already running
"""

import json

from django.core.management.base import BaseCommand

from apps.core.feeds import FeedLocked, advisory_lock
from apps.legal_work.archived_topic_match_sync import LOCK_NAME, run_archive_matching
from apps.legal_work.models import SyncResult
from apps.legal_work.sync import EXIT_LOCKED


class Command(BaseCommand):
    help = (
        "Match consultation-eligible legal records the current listing could "
        "not answer against the Koda.ee 'Hetkel käsil' archive."
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
            # Its own lock, distinct from the archive collector's and from both
            # current-topic jobs', so none of the four can block another.
            with advisory_lock(LOCK_NAME):
                report = run_archive_matching(dry_run=options["dry_run"])
        except FeedLocked as error:
            self._emit(
                as_json,
                {"result": "locked", "detail": str(error)},
                f"Vahele jäetud: {error}",
                style=self.style.WARNING,
            )
            raise SystemExit(EXIT_LOCKED) from None

        payload = report.as_dict()
        if report.result == SyncResult.FAILED:
            self._emit(as_json, payload, report.detail, style=self.style.ERROR)
            raise SystemExit(report.exit_code)

        message = report.detail
        if report.result == SyncResult.IMPORTED:
            message = (
                f"{report.detail} Vaadatud: {report.considered_items}. "
                f"Seotud: {report.matched}, ebaselgeid: {report.ambiguous}, "
                f"sidumata: {report.unmatched}."
            )
        self._emit(as_json, payload, message, style=self.style.SUCCESS)

    def _emit(self, as_json: bool, payload: dict, message: str, *, style) -> None:
        if as_json:
            # Result, dry-run flag, snapshot id, four counts and the archive
            # matcher version. No topic, candidate title, URL or evidence text.
            self.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            self.stdout.write(style(message))
