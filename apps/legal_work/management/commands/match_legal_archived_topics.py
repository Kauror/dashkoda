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

from django.core.management.base import BaseCommand

from apps.core.feed_commands import FeedCommandOutputMixin
from apps.core.feeds import FeedLocked, advisory_lock
from apps.legal_work.archived_topic_match_sync import LOCK_NAME, run_archive_matching
from apps.legal_work.models import SyncResult


class Command(FeedCommandOutputMixin, BaseCommand):
    help = (
        "Match consultation-eligible legal records the current listing could "
        "not answer against the Koda.ee 'Hetkel käsil' archive."
    )

    def add_arguments(self, parser):
        self.add_output_arguments(
            parser, dry_run_help="Compute every decision without publishing a snapshot."
        )

    def handle(self, *args, **options):
        as_json = options["as_json"]
        try:
            # Its own lock, distinct from the archive collector's and from both
            # current-topic jobs', so none of the four can block another.
            with advisory_lock(LOCK_NAME):
                report = run_archive_matching(dry_run=options["dry_run"])
        except FeedLocked as error:
            self.exit_locked(error, as_json=as_json)

        # Result, dry-run flag, snapshot id, four counts and the archive
        # matcher version. No topic, candidate title, URL or evidence text.
        payload = report.as_dict()
        if report.result == SyncResult.FAILED:
            self.emit(as_json, payload, report.detail, style=self.style.ERROR)
            raise SystemExit(report.exit_code)

        message = report.detail
        if report.result == SyncResult.IMPORTED:
            message = (
                f"{report.detail} Vaadatud: {report.considered_items}. "
                f"Seotud: {report.matched}, ebaselgeid: {report.ambiguous}, "
                f"sidumata: {report.unmatched}."
            )
        self.emit(as_json, payload, message, style=self.style.SUCCESS)
